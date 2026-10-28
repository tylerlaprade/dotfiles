import type { On, PromptOrigin } from "claude-code";
import type { Engine } from "claude-code/testing";
import { expect, test as it } from "claude-code/testing";

const SURFACES = ["terminal", "desktop", "vscode", "mobile"] as const;
const ENGINE_ROW = "engine row";
const AFTERNOON = /PM/u;
const MORNING = /AM/u;

const standInForEngine = (on: On): void => {
  on("session.append", (_, e, next) => next(e));
  on("ui.render", (engine, e) => {
    const { Text } = engine.ui.resolve(e);
    return <Text>{ENGINE_ROW}</Text>;
  });
};

const sendPrompt = (
  $: Engine,
  origin: PromptOrigin,
  uuid: string,
): ReturnType<Engine["session"]["append"]> =>
  $.session.append({
    message: {
      type: "user",
      role: "user",
      content: [{ type: "text", text: "hello" }],
    },
    door: "prompt",
    origin,
    uuid,
  });

const row = (origin: PromptOrigin, requestId: string) =>
  ({
    plugin: "sent-time",
    component: "UserMessage",
    requestId,
    props: { text: "hello", origin, isExpanded: false },
  }) as const;

const personRowsShowTheTime = async ($: Engine, on: On): Promise<void> => {
  standInForEngine(on);
  on("clock.now", () => ({ value: new Date(2026, 9, 28, 19, 4).getTime() }));
  const sent = [
    ["composer", "typed"],
    ["bridge", "phone"],
  ] as const;
  await Promise.all(sent.map(([kind, uuid]) => sendPrompt($, { kind }, uuid)));

  const shown = await Promise.all(
    SURFACES.flatMap((surface) =>
      sent.map(async ([kind, uuid]) => {
        const ui = await $.ui.mount({ ...row({ kind }, uuid), surface });
        const engineRow = await ui.find({ type: "Text", text: ENGINE_ROW });
        const time = await ui.find({ type: "Text", text: AFTERNOON });
        await ui.unmount();
        return [engineRow !== undefined, time?.text];
      }),
    ),
  );
  expect(shown).toEqual(shown.map(() => [true, "7:04 PM"]));
};

const otherRowsShowNoTime = async ($: Engine, on: On): Promise<void> => {
  standInForEngine(on);
  on("clock.now", () => ({ value: new Date(2026, 9, 28, 9, 30).getTime() }));
  const kinds = [
    "peer",
    "task-notification",
    "scheduled-trigger",
    "auto-continuation",
  ] as const;
  await Promise.all(kinds.map((kind) => sendPrompt($, { kind }, kind)));

  const shown = await Promise.all(
    kinds.map(async (kind) => {
      const ui = await $.ui.mount({
        ...row({ kind }, kind),
        surface: "terminal",
      });
      const engineRow = await ui.find({ type: "Text", text: ENGINE_ROW });
      const time = await ui.find({ type: "Text", text: MORNING });
      await ui.unmount();
      return [engineRow !== undefined, time];
    }),
  );
  expect(shown).toEqual(kinds.map(() => [true, undefined]));
};

it(
  "shows the send time under a message the person sent, on every surface",
  personRowsShowTheTime,
);
it("shows no time under a message from anyone else", otherRowsShowNoTime);
