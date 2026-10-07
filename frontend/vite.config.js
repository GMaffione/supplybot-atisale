import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In sviluppo il front end gira su :5173 e inoltra le chiamate /api al back end Node su :3001,
// cosi' il browser parla con un'unica origine e non servono regole CORS.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://localhost:3001", changeOrigin: true } },
  },
});
