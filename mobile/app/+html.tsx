import { ScrollViewStyleReset } from 'expo-router/html';
import { type PropsWithChildren } from 'react';

// Root HTML wrapper for the web export (expo-router convention: app/+html.tsx).
// Overrides expo-router's default <head> (which has no manifest link) to make
// the PWA installable — without this, mobile/public/manifest.json is built and
// served correctly but browsers never discover it, so no install prompt appears.
export default function Root({ children }: PropsWithChildren) {
  return (
    <html lang="en">
      <head>
        <meta charSet="utf-8" />
        <meta httpEquiv="X-UA-Compatible" content="IE=edge" />
        <meta name="viewport" content="width=device-width, initial-scale=1, shrink-to-fit=no" />
        <meta name="theme-color" content="#1863DC" />
        <link rel="manifest" href="/manifest.json" />
        <link rel="icon" href="/icon-192.png" />
        <link rel="apple-touch-icon" href="/icon-192.png" />
        <meta name="apple-mobile-web-app-capable" content="yes" />
        <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
        <meta name="apple-mobile-web-app-title" content="PetBMS" />
        <ScrollViewStyleReset />
      </head>
      <body>{children}</body>
    </html>
  );
}
