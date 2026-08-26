import Link from 'next/link';

import { ThemeToggle } from '@/components/theme-toggle';

export default function HomePage() {
  return (
    <main className="min-h-screen bg-bg p-6 text-text">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl">PanelPilot</h1>
        <ThemeToggle />
      </div>
      <p className="max-w-2xl text-text-muted">
        Diagnostic and design copilot for electrical and control engineers.
      </p>
      <p className="mt-4">
        <Link className="text-accent hover:text-accent-hover" href="/tokens">
          Design tokens
        </Link>
      </p>
    </main>
  );
}
