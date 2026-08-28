import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import basicSsl from '@vitejs/plugin-basic-ssl'

// HTTPS is NOT optional: the Perfect Corp Camera Kit refuses to initialise over
// plain HTTP, and getUserMedia is gated on a secure context. Testing on
// localhost hides HTTPS, CORS and camera-permission bugs that surface later at
// exactly the wrong moment — see implementation.md Step 11c.
export default defineConfig({
  plugins: [react(), basicSsl()],
  server: {
    https: true,
    host: true, // bind 0.0.0.0 so a phone on the same network can reach it
    port: 5173,
  },
  build: { outDir: 'dist', sourcemap: true },
})
