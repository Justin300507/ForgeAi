import axios from "axios";

const api = axios.create({ baseURL: import.meta.env.VITE_API_URL ?? "" });

api.interceptors.request.use((cfg) => {
  const token = localStorage.getItem("token");
  if (token) cfg.headers.Authorization = `Bearer ${token}`;
  return cfg;
});

export const authAPI = {
  register: (email, password) => api.post("/register", { email, password }),
  login: async (email, password) => {
    const form = new URLSearchParams({ username: email, password });
    const res = await api.post("/login", form, { headers: { "Content-Type": "application/x-www-form-urlencoded" } });
    return res;
  },
  me: () => api.get("/me"),
};

export const jobsAPI = {
  list: () => api.get("/jobs"),
  create: (idea, provider = "cerebras", deploy_to = "none", frontend_target = "web") =>
    api.post("/jobs", { idea, provider, deploy_to, frontend_target }),
  get: (id) => api.get(`/jobs/${id}`),
  cancel: (id) => api.post(`/jobs/${id}/cancel`),
  retry: (id) => api.post(`/jobs/${id}/retry`),
  checkDeployed: (id) => api.post(`/jobs/${id}/check-deployed`),
  checkStatus: (id) => api.get(`/jobs/${id}/check-status`),
};

export const credentialsAPI = {
  get: () => api.get("/credentials"),
  save: (data) => api.post("/credentials", data),
};

export default api;
