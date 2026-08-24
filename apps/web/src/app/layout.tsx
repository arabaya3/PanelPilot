import type { ReactNode } from 'react';

export const metadata = {
  title: 'PanelPilot',
  description: 'Diagnostic and design copilot for electrical and control engineers.',
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
