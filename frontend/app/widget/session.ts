// Only a conversation identifier is shared for UI invalidation. Credentials stay
// in the chat component's memory and never enter storage or cross-tab messages.
const identities = new Map<string, string | null>();
const listeners = new Set<() => void>();
export function customerIdentity(key: string) {
  return identities.get(key) ?? null;
}
export function subscribeCustomer(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
export function publishCustomer(key: string, conversation: string | null) {
  identities.set(key, conversation);
  for (const listener of listeners) listener();
}
