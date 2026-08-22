import { writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const { createCapabilityGuard } = await import(process.argv[2]);

const stateRoot = process.argv[3];
const root = process.argv[4];
const token = process.argv[5];
const actions = JSON.parse(process.argv[6]);
const guard = createCapabilityGuard({ stateRoot });
const results = [];

for (const action of actions) {
  try {
    if (action.kind === "open") {
      const authorization = await guard.authorizeOpen({
        path: root,
        mode: action.mode ?? "checkout",
        capabilityToken: token,
      });
      guard.bind(action.workspaceId, authorization);
      results.push({
        ok: true,
        access: authorization.access,
        subjectId: authorization.subjectId,
      });
    } else if (action.kind === "read") {
      await guard.authorizeRead(action.workspaceId, action.path);
      await guard.attestRead(action.workspaceId, action.path);
      results.push({ ok: true });
    } else if (action.kind === "recursiveRead") {
      await guard.authorizeRecursiveRead(action.workspaceId, action.path);
      results.push({ ok: true });
    } else if (action.kind === "filterReadable") {
      const paths = await guard.filterReadable(action.workspaceId, action.paths, (value) => value);
      results.push({ ok: true, paths });
    } else if (action.kind === "review") {
      await guard.authorizeReview(action.workspaceId);
      results.push({ ok: true });
    } else if (action.kind === "write") {
      await guard.authorizeWrite(action.workspaceId, action.path);
      results.push({ ok: true });
    } else if (action.kind === "patch") {
      await guard.authorizePatch(action.workspaceId, action.actions);
      results.push({ ok: true });
    } else if (action.kind === "command") {
      await guard.authorizeCommand(action.workspaceId, action.command);
      results.push({ ok: true });
    } else if (action.kind === "hostWrite") {
      await writeFile(resolve(root, action.path), action.content, "utf8");
      results.push({ ok: true });
    }
  } catch (error) {
    results.push({ ok: false, code: error.code ?? "UNKNOWN" });
  }
}

process.stdout.write(JSON.stringify(results));
