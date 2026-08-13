/** Backend connection constants. Override with VITE_BACKEND_ORIGIN at build time. */
const configuredBackendOrigin = import.meta.env.VITE_BACKEND_ORIGIN?.trim();
export const BACKEND_ORIGIN = (configuredBackendOrigin || 'http://127.0.0.1:12393').replace(
  /\/$/,
  '',
);

/** Build an absolute URL for a backend-relative path (e.g. "/live2d-models/..."). */
export function resolveBackendUrl(path: string): string {
  if (/^https?:\/\//i.test(path)) return path;
  return `${BACKEND_ORIGIN}${path.startsWith('/') ? path : `/${path}`}`;
}
