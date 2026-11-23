const installer = "https://github.com/andresgarcia29/omniroute-clients/releases/latest/download/install.sh";

export default {
  fetch(request) {
    if (!["GET", "HEAD"].includes(request.method)) {
      return new Response(null, { status: 405, headers: { Allow: "GET, HEAD" } });
    }
    if (new URL(request.url).pathname !== "/") {
      return new Response("Not found", { status: 404 });
    }
    return new Response(null, {
      status: 302,
      headers: { Location: installer, "Cache-Control": "no-store" },
    });
  },
};
