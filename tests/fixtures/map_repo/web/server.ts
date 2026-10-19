// The web server mentions the Python names parse and Config, as a client of the API.
export function startServer(port: number) {
  return { port, parse: true, Config: "default" };
}
