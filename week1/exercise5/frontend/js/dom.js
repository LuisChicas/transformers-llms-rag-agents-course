const SVG_NS = "http://www.w3.org/2000/svg";

/** Create an HTML element. Children may be nodes, strings, or null (skipped). */
export function h(tag, attrs = {}, ...children) {
  const element = document.createElement(tag);
  setAttrs(element, attrs);
  element.append(...children.filter((child) => child !== null && child !== undefined && child !== false));
  return element;
}

/** Create an SVG element. */
export function s(tag, attrs = {}, ...children) {
  const element = document.createElementNS(SVG_NS, tag);
  setAttrs(element, attrs);
  element.append(...children.filter((child) => child !== null && child !== undefined && child !== false));
  return element;
}

function setAttrs(element, attrs) {
  for (const [name, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    element.setAttribute(name, value === true ? "" : String(value));
  }
}

const numberFormat = new Intl.NumberFormat();

export function formatNumber(value) {
  return numberFormat.format(value);
}
