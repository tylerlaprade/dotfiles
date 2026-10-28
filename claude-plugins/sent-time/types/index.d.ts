export type SentTimes = Record<string, number>;

declare module "claude-code" {
  interface PluginState {
    "sent-time": { sentAt: SentTimes };
  }
}
