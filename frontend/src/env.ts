/** Backend connection constants. */
export const BACKEND_ORIGIN = 'http://127.0.0.1:12393';

/** Build an absolute URL for a backend-relative path (e.g. "/live2d-models/..."). */
export function resolveBackendUrl(path: string): string {
  if (/^https?:\/\//i.test(path)) return path;
  return `${BACKEND_ORIGIN}${path.startsWith('/') ? path : `/${path}`}`;
}
