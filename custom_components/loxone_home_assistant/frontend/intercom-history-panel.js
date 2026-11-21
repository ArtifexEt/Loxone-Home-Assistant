import "./intercom-gallery.js";

const labels = {
  en: {
    back: "Back to device",
    loading: "Loading photo history…",
    error: "Could not open the photo history.",
    retry: "Try again",
  },
  pl: {
    back: "Wróć do urządzenia",
    loading: "Wczytywanie historii zdjęć…",
    error: "Nie udało się otworzyć historii zdjęć.",
    retry: "Spróbuj ponownie",
  },
  de: {
    back: "Zurück zum Gerät",
    loading: "Fotoverlauf wird geladen…",
    error: "Der Fotoverlauf konnte nicht geöffnet werden.",
    retry: "Erneut versuchen",
  },
};

class LoxoneIntercomHistoryPanel extends HTMLElement {
  constructor() {
    super();
    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = `
      <style>
        :host { display: block; }
        [hidden] { display: none !important; }
        main { max-width: 900px; margin: 0 auto; padding: 16px; }
        .back {
          display: inline-flex;
          align-items: center;
          gap: 8px;
          min-height: 40px;
          margin-bottom: 8px;
        }
        a, button { color: var(--primary-color); }
        button {
          background: none;
          border: 1px solid currentColor;
          border-radius: 8px;
          font: inherit;
          min-height: 40px;
          padding: 0 12px;
          cursor: pointer;
        }
        :is(a, button):focus-visible { outline: 2px solid currentColor; outline-offset: 3px; }
      </style>
      <main>
        <a class="back" hidden><ha-icon icon="mdi:arrow-left" aria-hidden="true"></ha-icon><span></span></a>
        <div class="gallery"></div>
        <p role="status" hidden></p>
        <button type="button" hidden></button>
      </main>
    `;
    this._back = root.querySelector(".back");
    this._backLabel = this._back.querySelector("span");
    this._content = root.querySelector(".gallery");
    this._status = root.querySelector("[role=status]");
    this._retry = root.querySelector("button");
    this._retry.addEventListener("click", () => {
      this._request = undefined;
      this._load();
    });
  }

  set hass(hass) {
    this._hass = hass;
    if (this._gallery) this._gallery.hass = hass;
    this._renderLabels();
    this._load();
  }

  set route(route) {
    const deviceId = /^\/([0-9a-f]{32})\/?$/.exec(route.path)?.[1] || "";
    if (deviceId === this._deviceId) return;
    this._deviceId = deviceId;
    this._reset();
    this._load();
  }

  connectedCallback() {
    this._load();
  }

  disconnectedCallback() {
    this._reset();
  }

  _reset() {
    this._request?.abort();
    this._request = this._gallery = this._language = this._statusKey = undefined;
    this._content.replaceChildren();
  }

  _renderLabels() {
    const locale = this._language || this._hass?.locale?.language || this._hass?.language || "en";
    const text = labels[locale.toLowerCase().split("-")[0]] || labels.en;
    const displayBack = Boolean(this._deviceId && this._hass?.user?.is_admin);
    this._back.hidden = !displayBack;
    if (displayBack) this._back.href = `/config/devices/device/${this._deviceId}`;
    this._backLabel.textContent = text.back;
    this._status.hidden = !this._statusKey;
    this._status.textContent = this._statusKey ? text[this._statusKey] : "";
    this._retry.textContent = text.retry;
    this._retry.hidden = !this._deviceId || this._statusKey !== "error";
  }

  async _load() {
    if (!this.isConnected || !this._hass || this._deviceId === undefined || this._request) return;
    if (!this._deviceId) {
      this._statusKey = "error";
      this._renderLabels();
      return;
    }

    const controller = new AbortController();
    this._request = controller;
    const { signal } = controller;
    this._statusKey = "loading";
    this._renderLabels();

    try {
      const response = await this._hass.fetchWithAuth(
        `/api/loxone_home_assistant/intercom_history_device/${this._deviceId}`,
        { signal, cache: "no-store", redirect: "error" },
      );
      if (!response.ok) throw new Error(`Intercom history HTTP ${response.status}`);
      const { entity_id, language } = await response.json();
      if (signal.aborted) return;
      if (Object.hasOwn(labels, language)) this._language = language;
      const gallery = document.createElement("loxone-intercom-gallery");
      gallery.setConfig({ entity: entity_id });
      gallery.hass = this._hass;
      this._gallery = gallery;
      this._content.replaceChildren(gallery);
      this._statusKey = undefined;
    } catch {
      if (!signal.aborted) this._statusKey = "error";
    }
    if (!signal.aborted) this._renderLabels();
  }
}

if (!customElements.get("loxone-intercom-history-panel")) {
  customElements.define("loxone-intercom-history-panel", LoxoneIntercomHistoryPanel);
}
