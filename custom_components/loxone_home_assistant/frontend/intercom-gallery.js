const GALLERY_PATH = "/api/loxone_home_assistant/intercom_gallery";
const THUMBNAIL_CONCURRENCY = 4;

const labels = {
  en: {
    title: "Photos",
    refresh: "Refresh photos",
    previous: "Previous page",
    next: "Next page",
    page: (page, pages) => `Page ${page} of ${pages}`,
    loading: "Loading photos…",
    empty: "No photos in the archive.",
    error: "Could not load the photo archive.",
    thumbnailError: "Thumbnail unavailable",
    open: (date) => `Open photo from ${date}`,
    close: "Close preview",
    download: "Download photo",
    photo: "Intercom photo",
    photoLoading: "Loading photo…",
    photoError: "Could not load the photo.",
  },
  pl: {
    title: "Zdjęcia",
    refresh: "Odśwież zdjęcia",
    previous: "Poprzednia strona",
    next: "Następna strona",
    page: (page, pages) => `Strona ${page} z ${pages}`,
    loading: "Wczytywanie zdjęć…",
    empty: "W archiwum nie ma zdjęć.",
    error: "Nie udało się wczytać archiwum zdjęć.",
    thumbnailError: "Brak miniatury",
    open: (date) => `Otwórz zdjęcie: ${date}`,
    close: "Zamknij podgląd",
    download: "Pobierz zdjęcie",
    photo: "Zdjęcie z domofonu",
    photoLoading: "Wczytywanie zdjęcia…",
    photoError: "Nie udało się wczytać zdjęcia.",
  },
  de: {
    title: "Fotos",
    refresh: "Fotos aktualisieren",
    previous: "Vorherige Seite",
    next: "Nächste Seite",
    page: (page, pages) => `Seite ${page} von ${pages}`,
    loading: "Fotos werden geladen…",
    empty: "Im Archiv sind keine Fotos vorhanden.",
    error: "Das Fotoarchiv konnte nicht geladen werden.",
    thumbnailError: "Vorschaubild nicht verfügbar",
    open: (date) => `Foto vom ${date} öffnen`,
    close: "Vorschau schließen",
    download: "Foto herunterladen",
    photo: "Foto der Türsprechanlage",
    photoLoading: "Foto wird geladen…",
    photoError: "Das Foto konnte nicht geladen werden.",
  },
};

const template = document.createElement("template");
template.innerHTML = `
  <style>
    :host { display: block; min-inline-size: 0; }
    [hidden] { display: none !important; }
    ha-card { padding: 12px; }
    .toolbar, .paging, .preview-toolbar, .preview-actions {
      display: flex;
      align-items: center;
      gap: 4px;
    }
    .toolbar { flex-wrap: wrap; }
    h2 { margin: 0; font-size: 1rem; font-weight: 600; }
    .paging { margin-inline-start: auto; }
    .page-number {
      color: var(--secondary-text-color);
      font-size: 0.8125rem;
      font-variant-numeric: tabular-nums;
      white-space: nowrap;
    }
    button {
      color: inherit;
      font: inherit;
      background: none;
      border: 0;
      cursor: pointer;
    }
    button:focus-visible, a:focus-visible {
      outline: 2px solid var(--primary-color, #03a9f4);
      outline-offset: 2px;
    }
    .icon-button {
      display: grid;
      place-items: center;
      inline-size: 40px;
      block-size: 40px;
      padding: 0;
      border-radius: 50%;
      --mdc-icon-size: 20px;
    }
    .icon-button.download { color: inherit; }
    :host(:dir(rtl)) .page-arrow { transform: scaleX(-1); }
    .icon-button[aria-disabled="true"] { cursor: default; opacity: 0.38; }
    .gallery {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(min(100%, 7rem), 1fr));
      gap: 8px;
      list-style: none;
      padding: 0;
      margin: 8px 0 0;
    }
    .gallery:empty { display: none; }
    .tile {
      display: block;
      inline-size: 100%;
      overflow: hidden;
      text-align: start;
      padding: 0;
      border: 1px solid var(--divider-color, #d9d9d9);
      border-radius: 10px;
    }
    @media (hover: hover) {
      .tile:hover { border-color: var(--primary-color, #03a9f4); }
      .icon-button:not([aria-disabled="true"]):hover {
        background: var(--secondary-background-color, #f0f0f0);
      }
    }
    .frame {
      display: grid;
      place-items: center;
      aspect-ratio: 16 / 10;
      color: var(--secondary-text-color);
      background: var(--secondary-background-color, #f0f0f0);
      font-size: 0.75rem;
      text-align: center;
      overflow: hidden;
    }
    .frame > * { grid-area: 1 / 1; min-inline-size: 0; min-block-size: 0; }
    .frame img { display: block; inline-size: 100%; block-size: 100%; object-fit: cover; }
    .caption {
      display: flex;
      flex-wrap: wrap;
      column-gap: 6px;
      padding: 6px 8px;
      font-size: 0.75rem;
      font-variant-numeric: tabular-nums;
      line-height: 1.4;
    }
    .clock { color: var(--secondary-text-color); }
    .status { margin: 0; color: var(--secondary-text-color); }
    .status:not(:empty) { margin: 8px 0; }
    dialog {
      box-sizing: border-box;
      inline-size: min(calc(100% - 32px), 52rem);
      max-block-size: calc(100vh - 32px);
      max-block-size: calc(100dvh - 32px);
      padding: 12px;
      border: 1px solid var(--divider-color, #d9d9d9);
      border-radius: var(--ha-card-border-radius, 12px);
      background: var(--card-background-color, #fff);
      color: var(--primary-text-color, #222);
    }
    dialog::backdrop { background: rgb(0 0 0 / 60%); }
    .preview-toolbar { justify-content: space-between; margin-block-end: 8px; }
    .preview-time { font-size: 1rem; font-weight: 600; padding-inline-start: 4px; }
    .preview-image {
      display: block;
      inline-size: auto;
      max-inline-size: 100%;
      max-block-size: calc(100vh - 112px);
      max-block-size: calc(100dvh - 112px);
      object-fit: contain;
      margin: auto;
    }
  </style>
  <ha-card>
    <div class="toolbar">
      <h2></h2>
      <button class="icon-button refresh" type="button"><ha-icon icon="mdi:refresh" aria-hidden="true"></ha-icon></button>
      <div class="paging" hidden>
        <button class="icon-button previous" type="button"><ha-icon class="page-arrow" icon="mdi:chevron-left" aria-hidden="true"></ha-icon></button>
        <span class="page-number" role="status"></span>
        <button class="icon-button next" type="button"><ha-icon class="page-arrow" icon="mdi:chevron-right" aria-hidden="true"></ha-icon></button>
      </div>
    </div>
    <p class="status archive-status" role="status"></p>
    <ul class="gallery" role="list"></ul>
  </ha-card>
  <dialog aria-labelledby="preview-time">
    <div class="preview-toolbar">
      <time class="preview-time" id="preview-time"></time>
      <div class="preview-actions">
        <a class="icon-button download" hidden download><ha-icon icon="mdi:download" aria-hidden="true"></ha-icon></a>
        <button class="icon-button close" type="button" autofocus><ha-icon icon="mdi:close" aria-hidden="true"></ha-icon></button>
      </div>
    </div>
    <p class="status preview-status" role="status"></p>
    <img class="preview-image" hidden alt="">
  </dialog>
`;

function releaseImage(image) {
  const url = image.getAttribute("src");
  if (url) {
    image.removeAttribute("src");
    URL.revokeObjectURL(url);
  }
  image.hidden = true;
}

class LoxoneIntercomGallery extends HTMLElement {
  constructor() {
    super();
    const root = this.attachShadow({ mode: "open" });
    root.append(template.content.cloneNode(true));
    this._title = root.querySelector("h2");
    this._grid = root.querySelector(".gallery");
    this._status = root.querySelector(".archive-status");
    this._paging = root.querySelector(".paging");
    this._pageNumber = root.querySelector(".page-number");
    this._refresh = root.querySelector(".refresh");
    this._previous = root.querySelector(".previous");
    this._next = root.querySelector(".next");
    this._dialog = root.querySelector("dialog");
    this._previewTime = root.querySelector(".preview-time");
    this._previewStatus = root.querySelector(".preview-status");
    this._previewImage = root.querySelector(".preview-image");
    this._download = root.querySelector(".download");
    this._close = root.querySelector(".close");
    this._page = 1;
    this._pages = 1;

    this._refresh.addEventListener("click", () => {
      if (!this._loading && this._hass && this._entity) this._loadPage(1);
    });
    this._previous.addEventListener("click", () => {
      if (!this._loading && this._page > 1) this._loadPage(this._page - 1);
    });
    this._next.addEventListener("click", () => {
      if (!this._loading && this._page < this._pages) this._loadPage(this._page + 1);
    });
    this._close.addEventListener("click", () => this._closePreview(true));
    this._dialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      this._closePreview(true);
    });
    this._applyLanguage();
  }

  setConfig(config) {
    if (typeof config?.entity !== "string" || !/^camera\.[a-z0-9_]+$/.test(config.entity)) {
      throw new Error("Set entity to the Loxone Intercom Photo preview camera.");
    }
    if (config.entity === this._entity) return;
    this._entity = config.entity;
    this._preferredLanguage = undefined;
    this._applyLanguage();
    this._closePreview();
    this._clearPage();
    this._page = this._pages = 1;
    this._start();
  }

  set hass(hass) {
    this._hass = hass;
    this._applyLanguage();
    this._start();
  }

  connectedCallback() {
    this._start();
  }

  disconnectedCallback() {
    this._closePreview();
    this._clearPage();
  }

  getCardSize() {
    return 8;
  }

  _applyLanguage() {
    const language = this._preferredLanguage || this._hass?.locale?.language || this._hass?.language || "en";
    if (language === this._language) return;
    this._language = language;
    const primaryLanguage = language.toLowerCase().split("-")[0];
    this._labels = Object.hasOwn(labels, primaryLanguage) ? labels[primaryLanguage] : labels.en;
    this._title.textContent = this._labels.title;
    for (const key of ["refresh", "previous", "next", "close", "download"]) {
      this[`_${key}`].setAttribute("aria-label", this._labels[key]);
      this[`_${key}`].title = this._labels[key];
    }
    this._previewImage.alt = this._labels.photo;
    this._updatePaging();
  }

  _start() {
    if (this.isConnected && this._hass && this._entity && !this._pageRequest) {
      this._loadPage(this._page);
    }
  }

  _clearPage() {
    this._pageRequest?.abort();
    this._pageRequest = undefined;
    this._clearTiles();
  }

  _clearTiles() {
    for (const image of this._grid.querySelectorAll("img")) releaseImage(image);
    this._grid.replaceChildren();
  }

  _updatePaging() {
    this._paging.hidden = this._pages <= 1;
    this._refresh.setAttribute("aria-disabled", String(Boolean(this._loading)));
    this._previous.setAttribute("aria-disabled", String(this._loading || this._page <= 1));
    this._next.setAttribute("aria-disabled", String(this._loading || this._page >= this._pages));
    this._pageNumber.textContent = `${this._page} / ${this._pages}`;
    this._pageNumber.setAttribute("aria-label", this._labels.page(this._page, this._pages));
  }

  _request(path, signal) {
    return this._hass.fetchWithAuth(path, {
      signal,
      cache: "no-store",
      redirect: "error",
    });
  }

  async _loadPage(page) {
    this._pageRequest?.abort();
    const controller = new AbortController();
    this._pageRequest = controller;
    const { signal } = controller;
    const path = `${GALLERY_PATH}/${encodeURIComponent(this._entity)}`;
    this._loading = true;
    this._status.textContent = this._labels.loading;
    this._updatePaging();

    let tiles;
    try {
      const response = await this._request(`${path}?page=${page}`, signal);
      if (signal.aborted) return;
      if (!response.ok) {
        if (response.status !== 503) {
          this._page = this._pages = 1;
          this._clearTiles();
        }
        this._status.textContent = this._labels.error;
        return;
      }
      const result = await response.json();
      if (signal.aborted) return;
      this._preferredLanguage = Object.hasOwn(labels, result.language) ? result.language : undefined;
      this._applyLanguage();
      const locale = this._hass.locale;
      const timeZone = locale?.time_zone === "server" ? this._hass.config?.time_zone : undefined;
      const clockLanguage = locale?.time_format === "system" ? undefined : this._language;
      const hour12 = { 12: true, 24: false }[locale?.time_format];
      const day = new Intl.DateTimeFormat(this._language, { dateStyle: "medium", timeZone });
      const clock = new Intl.DateTimeFormat(clockLanguage, { timeStyle: "short", hour12, timeZone });
      tiles = result.items.map(({ id, timestamp }) => {
        const date = new Date(timestamp * 1000);
        return this._createTile(`${path}/${encodeURIComponent(id)}`, id, date, day, clock);
      });
      this._page = result.page;
      this._pages = Math.max(1, Math.ceil(result.total / result.page_size));
      this._clearTiles();
      this._grid.replaceChildren(...tiles.map((tile) => tile.item));
      this._status.textContent = tiles.length ? "" : this._labels.empty;
    } catch {
      if (!signal.aborted) this._status.textContent = this._labels.error;
    } finally {
      if (!signal.aborted) {
        this._loading = false;
        this._updatePaging();
      }
    }

    if (!tiles) return;
    for (let start = 0; start < tiles.length && !signal.aborted; start += THUMBNAIL_CONCURRENCY) {
      const batch = tiles.slice(start, start + THUMBNAIL_CONCURRENCY);
      await Promise.all(batch.map(async ({ photoPath, image, placeholder }) => {
        try {
          await this._loadImage(`${photoPath}?thumbnail=1`, image, signal);
        } catch {
          if (!signal.aborted) placeholder.textContent = this._labels.thumbnailError;
        }
      }));
    }
  }

  _createTile(photoPath, id, date, day, clock) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "tile";
    button.dataset.photoId = id;
    button.setAttribute("aria-haspopup", "dialog");
    const frame = document.createElement("span");
    frame.className = "frame";
    const image = document.createElement("img");
    image.alt = "";
    image.hidden = true;
    const placeholder = document.createElement("span");
    frame.append(image, placeholder);
    const caption = document.createElement("time");
    caption.className = "caption";
    caption.dateTime = date.toISOString();
    const dayText = document.createElement("span");
    dayText.textContent = day.format(date);
    const timeText = document.createElement("span");
    timeText.className = "clock";
    timeText.textContent = clock.format(date);
    caption.append(dayText, timeText);
    const dateLabel = `${dayText.textContent}, ${timeText.textContent}`;
    button.setAttribute("aria-label", this._labels.open(dateLabel));
    button.append(frame, caption);
    button.addEventListener("click", () => this._openPreview(photoPath, caption.dateTime, dateLabel, button));
    item.append(button);
    return { item, photoPath, image, placeholder };
  }

  async _loadImage(path, image, signal) {
    const response = await this._request(path, signal);
    if (!response.ok) throw new Error(`Intercom gallery HTTP ${response.status}`);
    const photo = await response.blob();
    if (signal.aborted) return;
    if (photo.type !== "image/jpeg" || !photo.size) throw new Error("Expected a JPEG photograph");
    image.src = URL.createObjectURL(photo);
    try {
      await image.decode();
    } catch (error) {
      if (!signal.aborted) releaseImage(image);
      throw error;
    }
    if (!signal.aborted) image.hidden = false;
  }

  async _openPreview(photoPath, dateTime, dateLabel, source) {
    const controller = new AbortController();
    this._previewRequest = controller;
    const { signal } = controller;
    this._previewSource = source;
    this._previewTime.dateTime = dateTime;
    this._previewTime.textContent = dateLabel;
    this._previewStatus.textContent = this._labels.photoLoading;
    this._dialog.showModal();
    try {
      await this._loadImage(photoPath, this._previewImage, signal);
      if (!signal.aborted) {
        this._previewStatus.textContent = "";
        this._download.href = this._previewImage.src;
        this._download.download = `intercom-${dateTime.replaceAll(":", "-")}.jpg`;
        this._download.hidden = false;
      }
    } catch {
      if (!signal.aborted) this._previewStatus.textContent = this._labels.photoError;
    }
  }

  _closePreview(restoreFocus = false) {
    this._previewRequest?.abort();
    this._previewRequest = undefined;
    if (this._dialog.open) this._dialog.close();
    this._download.hidden = true;
    this._download.removeAttribute("href");
    releaseImage(this._previewImage);
    if (restoreFocus) this._previewSource?.focus({ preventScroll: true });
    this._previewSource = undefined;
  }
}

if (!customElements.get("loxone-intercom-gallery")) {
  customElements.define("loxone-intercom-gallery", LoxoneIntercomGallery);
}
