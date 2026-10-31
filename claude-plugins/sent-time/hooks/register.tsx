import type { On, PromptOrigin, Register } from "claude-code";
import { atom, read, update } from "claude-code";

import type { SentTimes } from "../types/index.js";

const noTimes: SentTimes = {};
const sentAt = atom({ plugin: "sent-time", key: "sentAt" } as const, noTimes);

const personKinds: ReadonlySet<string> = new Set<PromptOrigin["kind"]>([
  "composer",
  "bridge",
]);

const clockTime = (ms: number): string => {
  const time = new Date(ms);
  const hours = time.getHours();
  const minutes = String(time.getMinutes()).padStart(2, "0");
  return `${hours % 12 || 12}:${minutes} ${hours < 12 ? "AM" : "PM"}`;
};

export const register: Register = (on: On) => {
  on("session.append", { door: "prompt" }, async ($, e, next) => {
    if (personKinds.has(e.origin.kind)) {
      const now = await $.clock.now();
      await update(
        $,
        sentAt,
        (times: SentTimes): SentTimes => ({
          ...times,
          [e.uuid]: now,
        }),
      );
    }
    return next(e);
  });

  on("ui.render", { component: "UserMessage" }, async ($, e, next) => {
    const drawn = await next(e);
    const sent = (await read($, sentAt))[e.requestId];
    if (sent === undefined) {
      return drawn;
    }
    const { Box, Text } = $.ui.resolve(e);
    return (
      <Box flexDirection="column">
        {drawn}
        <Box paddingLeft={2}>
          <Text dimColor={true}>{clockTime(sent)}</Text>
        </Box>
      </Box>
    );
  });
};
