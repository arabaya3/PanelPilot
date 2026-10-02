'use client';

import { useTranslations } from 'next-intl';
import { useState } from 'react';

/**
 * A file input in the app's own words and style.
 *
 * The browser's native control labels itself in the browser's language
 * ("Choose File", "No file chosen") whatever the page's locale, so the input
 * is visually hidden and a button-styled label stands in for it, with the
 * last file chosen named beside it.
 */
export function FilePicker({
  id,
  label,
  help,
  accept,
  disabled = false,
  onFile,
}: {
  id: string;
  label: string;
  help?: string;
  accept: string;
  disabled?: boolean;
  onFile: (file: File) => void;
}) {
  const t = useTranslations('files');
  const [chosen, setChosen] = useState<string | null>(null);

  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-sm font-semibold">
        {label}
      </label>
      <div className="flex flex-wrap items-center gap-2">
        <label
          htmlFor={id}
          aria-hidden="true"
          className={`btn btn-sm btn-secondary ${disabled ? 'pointer-events-none opacity-50' : 'cursor-pointer'}`}
        >
          {t('choose')}
        </label>
        <span className="text-sm text-text-muted" dir="auto">
          {chosen ?? t('none')}
        </span>
      </div>
      <input
        id={id}
        type="file"
        accept={accept}
        disabled={disabled}
        className="sr-only"
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) {
            setChosen(file.name);
            onFile(file);
          }
          // Cleared so choosing the same file again fires a change event.
          event.target.value = '';
        }}
      />
      {help && <p className="text-sm text-text-muted">{help}</p>}
    </div>
  );
}
