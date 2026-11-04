/// <reference types="vite/client" />

// Spec #113 — hls.js's `./light` export ships no declaration file of its
// own; it is the same class as the main entry, minus optional features.
declare module 'hls.js/light' {
  import Hls from 'hls.js'
  export default Hls
}
