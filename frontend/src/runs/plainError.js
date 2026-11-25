// What a person sees when a run action fails: the server's own plain sentence
// for a refused state (409), a short line otherwise. Never the raw error.
export function plainError(e) {
  if (e?.status === 409 && typeof e.message === 'string' && e.message.trim()) return e.message
  if (e?.status === 403) return "You don't have permission to do this."
  return "That didn't work. Try again."
}
