/**
 * Side panels that are columns on wide screens and slide-overs on narrow ones.
 *
 * Each panel is driven by two body classes: `<name>-closed` hides the column, and
 * `<name>-open-mobile` shows the slide-over. One backdrop is shared by all slide-overs.
 */
export function createPanelGroup(backdrop) {
  const panels = [];
  const body = document.body;

  const updateBackdrop = () => {
    backdrop.hidden = !panels.some((panel) => panel.isSlideOver() && panel.isVisible());
  };
  const closeSlideOvers = (except = null) => {
    for (const panel of panels) {
      if (panel !== except && panel.isSlideOver() && panel.isVisible()) panel.setVisible(false);
    }
  };

  backdrop.addEventListener("click", () => closeSlideOvers());
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeSlideOvers();
  });

  return {
    /** @param {{name: string, toggle: HTMLElement, closeButton: HTMLElement, query: string}} options */
    add({ name, toggle, closeButton, query }) {
      const slideOver = window.matchMedia(query);
      const panel = {
        isSlideOver: () => slideOver.matches,
        isVisible: () => (slideOver.matches
          ? body.classList.contains(`${name}-open-mobile`)
          : !body.classList.contains(`${name}-closed`)),
        setVisible(visible) {
          if (slideOver.matches) {
            if (visible) closeSlideOvers(panel);
            body.classList.toggle(`${name}-open-mobile`, visible);
          } else {
            body.classList.toggle(`${name}-closed`, !visible);
          }
          toggle.setAttribute("aria-expanded", String(visible));
          updateBackdrop();
        },
      };

      toggle.addEventListener("click", () => panel.setVisible(!panel.isVisible()));
      closeButton.addEventListener("click", () => panel.setVisible(false));
      slideOver.addEventListener("change", () => {
        body.classList.remove(`${name}-open-mobile`);
        toggle.setAttribute("aria-expanded", String(panel.isVisible()));
        updateBackdrop();
      });
      toggle.setAttribute("aria-expanded", String(panel.isVisible()));
      panels.push(panel);
      return panel;
    },
  };
}
