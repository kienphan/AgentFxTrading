/**
 * Oh My Pi Hook / Extension: Post-Feature Delivery Invariant
 * Checks git status at agent_end / turn_end.
 * Alerts if code was modified without committing or restarting agentfx.service / cbots.
 */

interface ExtensionContext {
  on(event: string, handler: () => Promise<void> | void): void;
  exec(command: string): Promise<{ stdout?: string; stderr?: string; exitCode?: number }>;
  logger: {
    warn(msg: string): void;
    info(msg: string): void;
    error(msg: string): void;
  };
}

export default function postFeatureDeliveryHook(pi: ExtensionContext): void {
  pi.on("agent_end", async () => {
    try {
      const res = await pi.exec("git status --porcelain");
      const uncommitted = (res?.stdout || "").trim();
      if (uncommitted.length > 0) {
        pi.logger.warn(
          "[HOOK post-feature-delivery] ⚠️ Uncommitted changes detected:\n" +
          uncommitted +
          "\n-> Mandatory steps: 1. git commit & push. 2. systemctl restart agentfx.service (if app changed). 3. docker restart <cbot> (if bot changed)."
        );
      }
    } catch (e) {
      // non-blocking
    }
  });
}
