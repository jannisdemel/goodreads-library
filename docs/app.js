const DATA_URL = "./data/books.json";
const ACTIONS_URL = "https://github.com/jannisdemel/goodreads-library/actions/workflows/sync.yml";

let allBooks = [];
let activeFilter = "all";
let viewMode = localStorage.getItem("viewMode") || "list";

async function init() {
  document.getElementById("sync-btn").href = ACTIONS_URL;
  applyViewMode();

  document.getElementById("view-toggle").addEventListener("click", () => {
    viewMode = viewMode === "grid" ? "list" : "grid";
    localStorage.setItem("viewMode", viewMode);
    applyViewMode();
    render();
  });

  try {
    const res = await fetch(DATA_URL);
    if (!res.ok) throw new Error(res.status);
    const data = await res.json();
    allBooks = data.books || [];

    const meta = document.getElementById("meta");
    meta.textContent = `Last synced: ${formatDate(data.generated_at)} · ${allBooks.length} books`;

    setupFilters();
    render();
  } catch (err) {
    document.getElementById("book-list").innerHTML =
      `<p class="empty">Could not load data. Run the sync first, or check the console.</p>`;
    console.error(err);
  }
}

function applyViewMode() {
  const main = document.getElementById("book-list");
  main.className = viewMode === "list" ? "view-list" : "";
  const btn = document.getElementById("view-toggle");
  btn.textContent = viewMode === "grid" ? "☰ List" : "⊞ Grid";
}

function formatDate(iso) {
  if (!iso) return "unknown";
  return new Date(iso).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" });
}

function formatDue(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" });
}

// Best status across all "work" library rows
function bookBestStatus(book) {
  const workRows = (book.library || []).filter(r => r.match_kind === "work");
  if (!workRows.length) return "not_found";
  const types = workRows.map(r => r.media_type);
  const statuses = workRows.map(r => r.status);
  const hasAvailBook = workRows.some(r => r.status === "available" && r.media_type === "book");
  const hasAvailAny  = workRows.some(r => r.status === "available");
  const hasBook      = types.includes("book");
  if (hasAvailBook)  return "avail_book";
  if (hasAvailAny)   return "avail_any";
  if (hasBook)       return "has_book";
  return "catalog";
}

function setupFilters() {
  document.getElementById("filters").addEventListener("click", e => {
    const btn = e.target.closest(".filter");
    if (!btn) return;
    document.querySelectorAll(".filter").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    activeFilter = btn.dataset.filter;
    render();
  });
}

function render() {
  const list = document.getElementById("book-list");

  const visible = allBooks.filter(b => {
    const s = bookBestStatus(b);
    switch (activeFilter) {
      case "all":        return true;
      case "avail_book": return s === "avail_book";
      case "avail_any":  return s === "avail_book" || s === "avail_any";
      case "in_catalog": return s !== "not_found";
      case "not_found":  return s === "not_found";
      default:           return true;
    }
  });

  if (!visible.length) {
    list.innerHTML = `<p class="empty">No books match this filter.</p>`;
    return;
  }

  list.innerHTML = visible.map(bookCard).join("");
}

// ── Book card ──────────────────────────────────────────────────────────────

function bookCard(book) {
  const coverHtml = book.cover_url
    ? `<img class="book-cover" src="${esc(book.cover_url)}" alt="${esc(book.title)}" loading="lazy" />`
    : `<div class="book-cover-placeholder">📚</div>`;

  const workRows  = (book.library || []).filter(r => r.match_kind === "work");
  const aboutRows = (book.library || []).filter(r => r.match_kind === "about");

  const libraryHtml = [
    ...workRows.map(r => libraryRow(r)),
    ...aboutRows.map(r => libraryRow(r)),
  ].join("");

  const searchLink = book.search_url
    ? `<a class="search-link" href="${esc(book.search_url)}" target="_blank" rel="noopener">Search in catalog ↗</a>`
    : "";

  const grLink = book.goodreads_url
    ? `<a class="gr-link" href="${esc(book.goodreads_url)}" target="_blank" rel="noopener">Goodreads ↗</a>`
    : "";

  const noMatch = book.library.length === 0
    ? `<p class="no-match">Not found in catalog</p>`
    : "";

  return `<article class="book-card">
    ${coverHtml}
    <div class="book-body">
      <div class="book-title">${esc(book.title)}</div>
      <div class="book-author">${esc(book.author)}</div>
      <div class="library-rows">${libraryHtml}${noMatch}</div>
      <div class="card-links">${searchLink}${grLink}</div>
    </div>
  </article>`;
}

// ── Library row ───────────────────────────────────────────────────────────

const TYPE_LABEL = {
  book:      "📖 Buch",
  ebook:     "📱 E-Book",
  audiobook: "🎧 Hörbuch",
  film:      "🎬 Film",
  other:     "📦",
};
const TYPE_CLASS = {
  book: "type-book", ebook: "type-ebook", audiobook: "type-audio",
  film: "type-film", other: "type-other",
};

function libraryRow(r) {
  const typeLabel = r.match_kind === "about"
    ? `📚 Über das Buch`
    : (TYPE_LABEL[r.media_type] || r.media_type);
  const typeClass = r.match_kind === "about" ? "type-about" : (TYPE_CLASS[r.media_type] || "type-other");

  const statusLabel = r.status === "available" ? "Verfügbar" : "Entliehen";
  const statusClass = r.status === "available" ? "status-avail" : "status-unavail";

  const dueHtml   = r.due_date ? `<span class="badge badge-due">bis ${formatDue(r.due_date)}</span>` : "";
  const bsHtml    = r.is_bestseller ? `<span class="badge badge-bestseller">⭐ Bestseller</span>` : "";
  const resHtml   = r.reservations > 0
    ? `<span class="badge badge-res">🔖 ${r.reservations} Vorb.</span>` : "";
  const copiesHtml = (r.copies_total > 1 && r.media_type !== "ebook")
    ? `<span class="copies">${r.copies_total}×</span>` : "";
  const langHtml  = r.language
    ? `<span class="badge lang-badge lang-${r.language.replace(/\s.*/, "")}">${esc(r.language)}</span>` : "";

  const shelf = r.locations.map(esc).join(" · ");
  const locHtml = shelf ? `<span class="shelf">${shelf}</span>` : "";

  const link = r.url
    ? `<a class="opac-link" href="${esc(r.url)}" target="_blank" rel="noopener">Katalog ↗</a>`
    : "";

  return `<div class="lib-row">
    <div class="lib-row-badges">
      <span class="badge type-badge ${typeClass}">${typeLabel}</span>
      ${langHtml}${bsHtml}
      <span class="badge ${statusClass}">${copiesHtml} ${statusLabel}</span>
      ${dueHtml}${resHtml}
    </div>
    <div class="lib-row-detail">${locHtml}${link}</div>
  </div>`;
}

// ── Util ──────────────────────────────────────────────────────────────────

function esc(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

init();
