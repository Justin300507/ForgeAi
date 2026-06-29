import axios from 'axios'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL ?? '',
})

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

api.interceptors.response.use(
  (res) => res,
  (err) => {
    // Only redirect on 401 if we had a token (expired session), not on failed login attempts
    if (err.response?.status === 401 && localStorage.getItem('token')) {
      localStorage.removeItem('token')
      window.location.href = '/login'
    }
    return Promise.reject(err)
  },
)

export default api

export function buildWsUrl(jobId) {
  if (import.meta.env.VITE_WS_URL)
    return `${import.meta.env.VITE_WS_URL}/ws/${jobId}`
  if (import.meta.env.VITE_API_URL)
    return `${import.meta.env.VITE_API_URL.replace(/^http/, 'ws')}/ws/${jobId}`
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${window.location.host}/ws/${jobId}`
}
