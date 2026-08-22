import { createHash, createHmac, timingSafeEqual } from "node:crypto";
import { lstat, readFile, realpath } from "node:fs/promises";
import { dirname, isAbsolute, join, relative, resolve } from "node:path";

const CONTRACT_SCHEMA = "codex.chatgpt.project-capability/v1";
const LEASE_SCHEMA = "codex.chatgpt.project-capability-lease/v1";
const TOKEN_SCHEMA = "codex.chatgpt.capability-token/v1";

export class CapabilityGuardError extends Error {
  constructor(code, message) {
    super(`${code}: ${message}`);
    this.code = code;
  }
}

function fail(code, message) {
  throw new CapabilityGuardError(code, message);
}

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function canonicalValue(value) {
  if (Array.isArray(value)) return value.map(canonicalValue);
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, canonicalValue(value[key])]),
    );
  }
  return value;
}

function canonicalBytes(value) {
  return Buffer.from(JSON.stringify(canonicalValue(value)), "utf8");
}

function inside(root, target) {
  const relationship = relative(root, target);
  return relationship === "" || (!relationship.startsWith("..") && !isAbsolute(relationship));
}

async function readJson(path, code) {
  let identity;
  try {
    identity = await lstat(path);
  } catch {
    fail(code, "required capability state is missing");
  }
  if (identity.isSymbolicLink() || !identity.isFile()) fail(code, "capability state is unsafe");
  try {
    return JSON.parse(await readFile(path, "utf8"));
  } catch {
    fail(code, "capability state is unreadable");
  }
}

async function readSecret(path) {
  let identity;
  try {
    identity = await lstat(path);
  } catch {
    fail("CAPABILITY_TOKEN_INVALID", "capability secret is missing");
  }
  if (
    identity.isSymbolicLink() ||
    !identity.isFile() ||
    (process.platform !== "win32" && (identity.mode & 0o077) !== 0)
  ) {
    fail("CAPABILITY_TOKEN_INVALID", "capability secret is unsafe");
  }
  const secret = await readFile(path);
  if (secret.length !== 32) fail("CAPABILITY_TOKEN_INVALID", "capability secret is invalid");
  return secret;
}

async function canonicalRoot(path) {
  try {
    return await realpath(path);
  } catch {
    fail("CAPABILITY_ROOT_MISMATCH", "workspace root is unavailable");
  }
}

function projectDirectory(stateRoot, root) {
  const key = sha256(Buffer.from(root.toLowerCase(), "utf8")).slice(0, 24);
  return join(stateRoot, "projects", key, "capabilities");
}

function tokenPayload(token, secret) {
  const parts = token.split(".");
  if (parts.length !== 2 || !parts[0] || !parts[1]) fail("CAPABILITY_TOKEN_INVALID", "token shape is invalid");
  const expected = createHmac("sha256", secret).update(parts[0], "ascii").digest();
  let supplied;
  try {
    supplied = Buffer.from(parts[1], "base64url");
  } catch {
    fail("CAPABILITY_TOKEN_INVALID", "token signature is invalid");
  }
  if (supplied.length !== expected.length || !timingSafeEqual(supplied, expected)) {
    fail("CAPABILITY_TOKEN_INVALID", "token signature is invalid");
  }
  try {
    return JSON.parse(Buffer.from(parts[0], "base64url").toString("utf8"));
  } catch {
    fail("CAPABILITY_TOKEN_INVALID", "token payload is invalid");
  }
}

function subjectRow(lease, payload, token) {
  if (!Array.isArray(lease.subjects)) fail("CAPABILITY_LEASE_UNRESOLVED", "lease subjects are invalid");
  const tokenHash = sha256(Buffer.from(token, "utf8"));
  const matches = lease.subjects.filter(
    (item) => item?.subject_id === payload.subject_id && item?.token_sha256 === tokenHash,
  );
  if (matches.length !== 1) fail("CAPABILITY_SUBJECT_MISMATCH", "subject token is not bound");
  return tokenHash;
}

async function loadAuthorization(stateRoot, root, token) {
  const secret = await readSecret(join(stateRoot, "capability-secret.key"));
  const payload = tokenPayload(token, secret);
  if (payload.schema !== TOKEN_SCHEMA || payload.project_root !== root) {
    fail("CAPABILITY_ROOT_MISMATCH", "token is bound to another root");
  }
  const directory = projectDirectory(stateRoot, root);
  const lease = await readJson(join(directory, "active-lease.json"), "CAPABILITY_LEASE_UNRESOLVED");
  if (
    lease.schema !== LEASE_SCHEMA ||
    lease.state !== "active" ||
    lease.lease_id !== payload.lease_id ||
    lease.capability_id !== payload.capability_id ||
    lease.project_root !== root
  ) {
    fail("CAPABILITY_LEASE_UNRESOLVED", "token has no active exact-root lease");
  }
  const capabilityPath = join(directory, "contracts", `${lease.capability_id}.json`);
  if (resolve(lease.capability_path) !== resolve(capabilityPath)) {
    fail("CAPABILITY_LEASE_UNRESOLVED", "capability path binding is invalid");
  }
  const contract = await readJson(capabilityPath, "CAPABILITY_LEASE_UNRESOLVED");
  if (
    contract.schema !== CONTRACT_SCHEMA ||
    sha256(canonicalBytes(contract)) !== lease.capability_sha256 ||
    contract.binding?.project_root !== root
  ) {
    fail("CAPABILITY_LEASE_UNRESOLVED", "capability contract binding is invalid");
  }
  return {
    root,
    access: contract.access,
    subjectId: payload.subject_id,
    leaseId: lease.lease_id,
    capabilityId: lease.capability_id,
    tokenHash: subjectRow(lease, payload, token),
    contract,
  };
}

function requiredReads(authorization) {
  const values = authorization.contract.binding?.required_reads;
  if (!Array.isArray(values)) fail("CAPABILITY_LEASE_UNRESOLVED", "required reads are invalid");
  const seen = new Set();
  return values.map((item) => {
    if (
      item === null ||
      typeof item !== "object" ||
      typeof item.path !== "string" ||
      typeof item.sha256 !== "string" ||
      !/^[0-9a-f]{64}$/.test(item.sha256)
    ) {
      fail("CAPABILITY_LEASE_UNRESOLVED", "required read binding is invalid");
    }
    const path = resolve(item.path);
    if (!inside(authorization.root, path) || seen.has(path)) {
      fail("CAPABILITY_LEASE_UNRESOLVED", "required read path is invalid");
    }
    seen.add(path);
    return { path, sha256: item.sha256 };
  });
}

async function requiredReadSha(path) {
  let identity;
  try {
    identity = await lstat(path);
  } catch {
    fail("CAPABILITY_ENTRY_CHANGED", `required read is unavailable: ${path}`);
  }
  if (identity.isSymbolicLink() || !identity.isFile()) {
    fail("CAPABILITY_ENTRY_CHANGED", `required read is unsafe: ${path}`);
  }
  return sha256(await readFile(path));
}

async function assertRequiredReads(authorization) {
  for (const item of requiredReads(authorization)) {
    if (authorization.attestedReads.get(item.path) !== item.sha256) {
      fail("CAPABILITY_ENTRY_ATTESTATION_REQUIRED", `read this exact file before writing: ${item.path}`);
    }
    if ((await requiredReadSha(item.path)) !== item.sha256) {
      fail("CAPABILITY_ENTRY_CHANGED", `required read changed after attestation: ${item.path}`);
    }
  }
}

async function safeWriteTarget(root, input) {
  if (typeof input !== "string" || input.includes("\0") || isAbsolute(input)) {
    fail("CAPABILITY_WRITE_OUT_OF_SCOPE", "write path must be relative");
  }
  const target = resolve(root, input);
  if (!inside(root, target)) fail("CAPABILITY_WRITE_OUT_OF_SCOPE", "write path escapes the root");
  let current = target;
  while (inside(root, current)) {
    try {
      if ((await lstat(current)).isSymbolicLink()) fail("CAPABILITY_SYMLINK_FORBIDDEN", "write path contains a symlink");
    } catch (error) {
      if (error instanceof CapabilityGuardError) throw error;
      if (error?.code !== "ENOENT") throw error;
    }
    if (current === root) break;
    current = dirname(current);
  }
  return target;
}

async function safeReadTarget(root, input) {
  if (typeof input !== "string" || input.includes("\0")) {
    fail("CAPABILITY_READ_FORBIDDEN", "read path is invalid");
  }
  const target = resolve(root, input);
  if (!inside(root, target)) fail("CAPABILITY_READ_FORBIDDEN", "read path escapes the root");
  try {
    const canonical = await realpath(target);
    if (!inside(root, canonical)) fail("CAPABILITY_READ_FORBIDDEN", "read path escapes the root");
    return canonical;
  } catch (error) {
    if (error instanceof CapabilityGuardError) throw error;
    if (error?.code === "ENOENT") return target;
    fail("CAPABILITY_READ_FORBIDDEN", "read path is unavailable");
  }
}

function subjectRoots(authorization, key) {
  const subjects = authorization.contract.subjects;
  if (subjects === undefined) return [];
  const lanes = subjects?.lanes;
  const merger = subjects?.merger;
  if (!Array.isArray(lanes) || merger === null || typeof merger !== "object") {
    fail("CAPABILITY_LEASE_UNRESOLVED", "capability subjects are invalid");
  }
  const candidates = [
    ...lanes.filter((item) => item?.id === authorization.subjectId),
    ...(merger?.id === authorization.subjectId ? [merger] : []),
  ];
  if (candidates.length !== 1) fail("CAPABILITY_SUBJECT_MISMATCH", "subject scope is not unique");
  const values = candidates[0]?.[key] ?? [];
  if (!Array.isArray(values) || values.some((item) => typeof item !== "string")) {
    fail("CAPABILITY_LEASE_UNRESOLVED", "subject paths are invalid");
  }
  return values;
}

function roots(authorization, key) {
  const values = authorization.contract.paths?.[key];
  if (!Array.isArray(values) || values.some((item) => typeof item !== "string")) {
    fail("CAPABILITY_LEASE_UNRESOLVED", "capability paths are invalid");
  }
  return [...values, ...subjectRoots(authorization, key)].map((item) => resolve(item));
}

async function isReadable(authorization, input) {
  let target;
  try {
    target = await safeReadTarget(authorization.root, input);
  } catch (error) {
    if (error instanceof CapabilityGuardError && error.code === "CAPABILITY_READ_FORBIDDEN") return false;
    throw error;
  }
  const denied = roots(authorization, "read_deny_roots");
  if (denied.some((item) => inside(item, target))) return false;
  const allowed = roots(authorization, "read_roots");
  return allowed.some((item) => inside(item, target));
}

export function createCapabilityGuard({ stateRoot }) {
  const state = resolve(stateRoot);
  const bindings = new Map();
  const openCounts = new Map();

  async function current(workspaceId) {
    const binding = bindings.get(workspaceId);
    if (!binding) fail("CAPABILITY_WORKSPACE_BINDING_REQUIRED", "workspace is not capability-bound");
    const refreshed = await loadAuthorization(state, binding.root, binding.token);
    if (
      refreshed.leaseId !== binding.leaseId ||
      refreshed.capabilityId !== binding.capabilityId ||
      refreshed.subjectId !== binding.subjectId
    ) {
      fail("CAPABILITY_WORKSPACE_BINDING_REQUIRED", "workspace binding changed");
    }
    return { ...refreshed, token: binding.token, attestedReads: binding.attestedReads };
  }

  return {
    async authorizeOpen({ path, mode, capabilityToken }) {
      if (mode !== "checkout") fail("CAPABILITY_WORKTREE_FORBIDDEN", "capability worktrees are disabled");
      if (typeof capabilityToken !== "string" || !capabilityToken) {
        fail("CAPABILITY_TOKEN_INVALID", "capability token is required");
      }
      const root = await canonicalRoot(path);
      const authorization = await loadAuthorization(state, root, capabilityToken);
      const key = `${authorization.leaseId}:${authorization.subjectId}`;
      const count = openCounts.get(key) ?? 0;
      if (count >= 2) fail("CAPABILITY_OPEN_RETRY_EXHAUSTED", "workspace open retry is exhausted");
      openCounts.set(key, count + 1);
      return { ...authorization, token: capabilityToken };
    },

    bind(workspaceId, authorization) {
      if (bindings.has(workspaceId)) fail("CAPABILITY_WORKSPACE_BINDING_REQUIRED", "workspace is already bound");
      bindings.set(workspaceId, { ...authorization, attestedReads: new Map() });
    },

    async authorizeWorkspace(workspaceId) {
      await current(workspaceId);
    },

    async authorizeRead(workspaceId, input) {
      const authorization = await current(workspaceId);
      if (!(await isReadable(authorization, input))) {
        fail("CAPABILITY_READ_FORBIDDEN", "read path is outside scope or denied");
      }
    },

    async filterReadable(workspaceId, values, pathOf) {
      if (!Array.isArray(values) || typeof pathOf !== "function") {
        fail("CAPABILITY_LEASE_UNRESOLVED", "open context candidates are invalid");
      }
      const authorization = await current(workspaceId);
      const visible = [];
      for (const value of values) {
        if (await isReadable(authorization, pathOf(value))) visible.push(value);
      }
      return visible;
    },

    async authorizeRecursiveRead(workspaceId, input) {
      const authorization = await current(workspaceId);
      const target = await safeReadTarget(authorization.root, input);
      const denied = roots(authorization, "read_deny_roots");
      if (denied.some((item) => inside(item, target) || inside(target, item))) {
        fail("CAPABILITY_READ_FORBIDDEN", "recursive read scope overlaps a denied path");
      }
      const allowed = roots(authorization, "read_roots");
      if (!allowed.some((item) => inside(item, target))) fail("CAPABILITY_READ_FORBIDDEN", "read path is outside scope");
    },

    async authorizeReview(workspaceId) {
      await current(workspaceId);
      fail("CAPABILITY_REVIEW_FORBIDDEN", "repository-wide change review is disabled for capability sessions");
    },

    async attestRead(workspaceId, input) {
      const authorization = await current(workspaceId);
      const target = await safeReadTarget(authorization.root, input);
      const required = requiredReads(authorization).filter((item) => item.path === target);
      if (required.length === 0) return;
      if (required.length !== 1) fail("CAPABILITY_LEASE_UNRESOLVED", "required read path is ambiguous");
      const actual = await requiredReadSha(target);
      if (actual !== required[0].sha256) {
        fail("CAPABILITY_ENTRY_CHANGED", `required read bytes changed: ${target}`);
      }
      authorization.attestedReads.set(target, actual);
    },

    async authorizeWrite(workspaceId, input) {
      const authorization = await current(workspaceId);
      if (!new Set(["bounded-write", "control-write"]).has(authorization.access)) {
        fail("CAPABILITY_WRITE_FORBIDDEN", "workspace is read-only");
      }
      await assertRequiredReads(authorization);
      const target = await safeWriteTarget(authorization.root, input);
      const denied = roots(authorization, "write_deny_roots");
      if (denied.some((item) => inside(item, target) || inside(target, item))) {
        fail("CAPABILITY_PATH_FORBIDDEN", "write path overlaps a denied path");
      }
      const allowed = roots(authorization, "write_roots");
      if (!allowed.some((item) => inside(item, target))) {
        fail("CAPABILITY_WRITE_OUT_OF_SCOPE", "write path is outside capability scope");
      }
    },

    async authorizePatch(workspaceId, actions) {
      const authorization = await current(workspaceId);
      if (!new Set(["bounded-write", "control-write"]).has(authorization.access)) {
        fail("CAPABILITY_WRITE_FORBIDDEN", "workspace is read-only");
      }
      if (!Array.isArray(actions) || actions.length === 0) fail("CAPABILITY_WRITE_OUT_OF_SCOPE", "patch actions are invalid");
      for (const action of actions) {
        await this.authorizeWrite(workspaceId, action.path);
        if (action.moveTo) await this.authorizeWrite(workspaceId, action.moveTo);
      }
    },

    async authorizeCommand(workspaceId) {
      const authorization = await current(workspaceId);
      if (authorization.contract.commands?.mode === "none") {
        fail("CAPABILITY_COMMAND_FORBIDDEN", "commands are disabled for this capability");
      }
      fail("CAPABILITY_COMMAND_SANDBOX_UNAVAILABLE", "exact command sandbox is unavailable");
    },
  };
}
