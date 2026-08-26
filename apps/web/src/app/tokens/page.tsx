import { ThemeToggle } from '@/components/theme-toggle';

/**
 * The token gallery — every token rendered as a swatch, in whichever theme is
 * active.
 *
 * This is the visual-regression baseline the later component work is checked
 * against. A route rather than a separate Storybook install: it renders in the
 * real application, with the real providers and the real stylesheet, so what
 * it shows is what ships. A parallel Storybook environment can drift from the
 * app it documents, and usually does.
 *
 * Toggle the theme and every swatch on this page moves, because each one reads
 * a variable rather than a colour.
 */

const SURFACES = [
  { token: '--color-bg', className: 'bg-bg', label: 'Background' },
  { token: '--color-surface', className: 'bg-surface', label: 'Surface' },
  { token: '--color-surface-raised', className: 'bg-surface-raised', label: 'Surface raised' },
  { token: '--color-border', className: 'bg-border', label: 'Border' },
];

const TEXT = [
  { token: '--color-text', className: 'text-text', label: 'Text' },
  { token: '--color-text-muted', className: 'text-text-muted', label: 'Text muted' },
];

const SEVERITIES = [
  {
    label: 'Critical',
    token: '--color-severity-critical',
    text: 'text-severity-critical',
    surface: 'bg-severity-critical-surface',
  },
  {
    label: 'Warning',
    token: '--color-severity-warning',
    text: 'text-severity-warning',
    surface: 'bg-severity-warning-surface',
  },
  {
    label: 'Info',
    token: '--color-severity-info',
    text: 'text-severity-info',
    surface: 'bg-severity-info-surface',
  },
];

// Strings rather than numbers: these are interpolated into a token name and
// a class name, and the lint rule that forbids implicit number-to-string
// conversion in a template is right — a locale-dependent `toString` in a
// class name would be a genuinely confusing bug.
const SPACES = ['1', '2', '3', '4', '5', '6', '7', '8'] as const;

const TYPE_SCALE = [
  { label: 'xs', className: 'text-xs' },
  { label: 'sm', className: 'text-sm' },
  { label: 'base', className: 'text-base' },
  { label: 'lg', className: 'text-lg' },
  { label: 'xl', className: 'text-xl' },
  { label: '2xl', className: 'text-2xl' },
];

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mb-7">
      <h2 className="mb-4 text-xl">{title}</h2>
      {children}
    </section>
  );
}

export default function TokensPage() {
  return (
    <main className="bg-bg p-6 text-text">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl">Design tokens</h1>
        <ThemeToggle />
      </div>

      <p className="mb-6 max-w-2xl text-text-muted">
        Every value below resolves to a CSS custom property. Switching the theme changes the
        variables, not the components — which is why nothing here hardcodes a colour.
      </p>

      <Section title="Surfaces">
        <div className="flex flex-wrap gap-4">
          {SURFACES.map((swatch) => (
            <div key={swatch.token} className="w-40">
              <div
                data-testid={`swatch-${swatch.token}`}
                className={`${swatch.className} h-8 rounded-md border border-border`}
              />
              <p className="mt-2 text-sm">{swatch.label}</p>
              <code className="text-xs text-text-muted">{swatch.token}</code>
            </div>
          ))}
        </div>
      </Section>

      <Section title="Text">
        <div className="flex flex-col gap-2">
          {TEXT.map((swatch) => (
            <p key={swatch.token} className={swatch.className}>
              {swatch.label} — <code className="text-xs">{swatch.token}</code>
            </p>
          ))}
        </div>
      </Section>

      <Section title="Severity">
        <div className="flex flex-wrap gap-4">
          {SEVERITIES.map((severity) => (
            <div
              key={severity.token}
              data-testid={`severity-${severity.label.toLowerCase()}`}
              className={`${severity.surface} w-56 rounded-md border border-border p-4`}
            >
              <p className={`${severity.text} text-lg`}>{severity.label}</p>
              <code className="text-xs text-text-muted">{severity.token}</code>
            </div>
          ))}
        </div>
      </Section>

      <Section title="Spacing">
        <div className="flex flex-col gap-2">
          {SPACES.map((step) => (
            <div key={step} className="flex items-center gap-4">
              <code className="w-24 text-xs text-text-muted">--space-{step}</code>
              <div
                data-testid={`space-${step}`}
                className="h-4 bg-accent"
                style={{ width: `var(--space-${step})` }}
              />
            </div>
          ))}
        </div>
      </Section>

      <Section title="Type scale">
        <div className="flex flex-col gap-2">
          {TYPE_SCALE.map((size) => (
            <p key={size.label} className={size.className}>
              {size.label} — the drive tripped on overcurrent
            </p>
          ))}
        </div>
      </Section>

      <Section title="Technical values">
        <p className="max-w-2xl text-text-muted">
          Codes and measurements render in the mono stack, because they are transcribed by hand into
          a keypad and a proportional font makes 0/O and 1/l ambiguous.
        </p>
        <p className="mt-2">
          Fault <code>F0001</code> · parameter <code>21.03</code> · supply <code>400 V</code>
        </p>
      </Section>

      <Section title="Accent">
        <div className="flex flex-wrap gap-4">
          <button
            type="button"
            className="rounded-md bg-accent px-4 py-2 text-accent-contrast hover:bg-accent-hover"
          >
            Accent action
          </button>
          <div className="rounded-md border border-border bg-surface px-4 py-2 shadow-md">
            Raised surface
          </div>
        </div>
      </Section>
    </main>
  );
}
