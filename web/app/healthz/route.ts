/**
 * Liveness probe for web/Dockerfile's HEALTHCHECK. Deliberately does no
 * backend I/O: probing "/" instead server-renders the home page, whose
 * getHealth()/listRuns() calls would make a slow backend flip a healthy
 * frontend to unhealthy (and scan the checkpoint store every 30s).
 */
export function GET() {
  return new Response("ok");
}
