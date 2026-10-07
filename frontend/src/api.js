// Thin wrapper around fetch so components stay readable and errors are consistent.
async function request(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail || detail;
    } catch {
      /* body was not JSON */
    }
    throw new Error(detail);
  }
  return res.json();
}

export const api = {
  clusters: (sort, level) =>
    request(`/api/clusters?sort=${sort}${level ? `&level=${level}` : ""}&limit=100`),
  detail: (id) => request(`/api/clusters/${id}`),
  trend: (id) => request(`/api/clusters/${id}/trend`),
  summarize: (id, refresh = false) =>
    request(`/api/clusters/${id}/summarize?refresh=${refresh}`, { method: "POST" }),
  reset: () => request("/api/reset", { method: "DELETE" }),
  ingest: (file) => {
    const form = new FormData();
    form.append("file", file);
    return request("/api/ingest", { method: "POST", body: form });
  },
};
