/**
 * Exporting schematic sheets: PNG per sheet, PDF through the print dialog.
 *
 * **Styles are inlined before a sheet leaves the page.** The symbols are
 * coloured by utility classes resolved from the design tokens; a serialised
 * `<svg>` carries the class names but not the stylesheet, so an image made
 * from it naively would lose every stroke — a blank rectangle that looks like
 * an export succeeded. Each element's computed paint is copied onto the clone
 * as attributes first.
 *
 * PDF goes through the browser's print dialog ("Save as PDF"). Each sheet is
 * its own printed page (`print:break-after-page`), which is exactly the
 * numbered-sheet split the layout already made, and needs no PDF library.
 */

/** Presentation properties that decide what a sheet looks like. */
const PAINT_PROPERTIES = [
  'fill',
  'stroke',
  'stroke-width',
  'stroke-dasharray',
  'font-family',
  'font-size',
  'font-weight',
  'opacity',
] as const;

/** How many pixels per sheet unit a PNG is rendered at: sharp when zoomed. */
export const PNG_SCALE = 2;

/**
 * Clone a sheet with every element's computed paint written onto it.
 *
 * @param source - The rendered sheet, attached to the document.
 * @param getStyle - Style resolver; injectable for tests.
 * @returns A detached clone that renders identically without the stylesheet.
 */
export function inlinedClone(
  source: SVGSVGElement,
  getStyle: (element: Element) => CSSStyleDeclaration = (element) =>
    window.getComputedStyle(element),
): SVGSVGElement {
  const clone = source.cloneNode(true) as SVGSVGElement;
  const originals = [source, ...Array.from(source.querySelectorAll('*'))];
  const copies = [clone, ...Array.from(clone.querySelectorAll('*'))];

  originals.forEach((original, index) => {
    const copy = copies[index];
    if (copy === undefined) return;
    const style = getStyle(original);
    for (const property of PAINT_PROPERTIES) {
      const value = style.getPropertyValue(property);
      if (value !== '') copy.setAttribute(property, value);
    }
    copy.removeAttribute('class');
  });

  clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
  return clone;
}

/**
 * Serialise a sheet to standalone SVG markup.
 *
 * @param sheet - The rendered sheet.
 * @returns SVG text that renders without the page's stylesheet.
 */
export function sheetToSvgText(sheet: SVGSVGElement): string {
  return new XMLSerializer().serializeToString(inlinedClone(sheet));
}

/**
 * Render a sheet to a PNG.
 *
 * @param sheet - The rendered sheet.
 * @param background - Colour painted behind it; PNGs are otherwise transparent,
 *   which prints as black on some viewers.
 * @returns The image.
 * @throws If the browser cannot rasterise the SVG.
 */
export async function sheetToPng(sheet: SVGSVGElement, background: string): Promise<Blob> {
  const width = sheet.viewBox.baseVal.width;
  const height = sheet.viewBox.baseVal.height;
  const url = URL.createObjectURL(
    new Blob([sheetToSvgText(sheet)], { type: 'image/svg+xml;charset=utf-8' }),
  );
  try {
    const image = await loadImage(url);
    const canvas = document.createElement('canvas');
    canvas.width = width * PNG_SCALE;
    canvas.height = height * PNG_SCALE;
    const context = canvas.getContext('2d');
    if (context === null) throw new Error('this browser cannot draw to a canvas');
    context.fillStyle = background;
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    return await new Promise<Blob>((resolve, reject) => {
      canvas.toBlob((blob) => {
        if (blob === null) reject(new Error('PNG encoding failed'));
        else resolve(blob);
      }, 'image/png');
    });
  } finally {
    URL.revokeObjectURL(url);
  }
}

/**
 * Offer a file to the user.
 *
 * @param blob - The contents.
 * @param filename - The suggested name.
 */
export function download(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  // Revoked on the next tick: revoking synchronously can cancel the download
  // before the browser has started reading the URL.
  setTimeout(() => {
    URL.revokeObjectURL(url);
  }, 0);
}

/**
 * A safe file name for a sheet.
 *
 * @param title - The drawing's title.
 * @param sheet - The sheet number.
 * @returns e.g. `pump-station-mcc-1-sheet-2.png`.
 */
export function sheetFilename(title: string, sheet: number): string {
  const slug = title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
  return `${slug === '' ? 'schematic' : slug}-sheet-${String(sheet)}.png`;
}

function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => {
      resolve(image);
    };
    image.onerror = () => {
      reject(new Error('the sheet could not be rasterised'));
    };
    image.src = url;
  });
}
