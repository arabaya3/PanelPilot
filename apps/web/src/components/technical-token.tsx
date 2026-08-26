import type { ReactNode } from 'react';

/**
 * An LTR island inside RTL prose.
 *
 * A fault code, a parameter number, a part number and a measured value are
 * read left to right in every language. Dropped unmarked into an Arabic or
 * Hebrew sentence, the bidirectional algorithm reorders them against the
 * surrounding text — and the failure is not a wrong font or a shifted margin,
 * it is **the wrong code**. "F0001" can render as "0001F", and an engineer
 * types what they see into a keypad.
 *
 * `<bdi>` is the mechanism, not a `<span dir="ltr">`. The two differ in a way
 * that matters: `bdi` also *isolates* the run, so the token cannot reorder the
 * text around it. A bare `dir` on a span fixes the token and leaves the
 * sentence it sits in scrambled.
 *
 * This pairs with AI-010, which keeps the same tokens untranslated on the way
 * out of the model. That guarantees the characters are right; this guarantees
 * they are displayed in the right order. Either one alone leaves the engineer
 * reading something the equipment will not recognise.
 */
export function TechnicalToken({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <bdi
      dir="ltr"
      // Mono, for the same reason the token file defines a mono stack: these
      // are transcribed by hand, and a proportional font makes 0/O and 1/l
      // ambiguous at exactly the moment that matters.
      className={className ? `font-mono ${className}` : 'font-mono'}
    >
      {children}
    </bdi>
  );
}
