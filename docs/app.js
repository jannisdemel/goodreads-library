const DATA_URL = "./data/books.json";
// Replace with your actual GitHub repo path once created:
const ACTIONS_URL = "https://github.com/jannisdemel/goodreads-library/actions/workflows/sync.yml";

let allBooks = [];
let activeFilter = "all";

async function init() {
  document.getElementById("sync-btn").href = ACTIONS_URL;

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

function formatDate(iso) {
  if (!iso) return "unknown";
  return new Date(iso).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" });
}

function bookBestStatus(book) {
  const statuses = (book.library || []).map(r => r.status);
  if (statuses.includes("available")) return "available";
  if (statuses.some(s => s === "unavailable")) return "in_catalog";
  return "not_found";
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
    if (activeFilter === "all") return true;
    if (activeFilter === "available") return s === "available";
    if (activeFilter === "in_catalog") return s === "available" || s === "in_catalog";
    if (activeFilter === "not_found") return s === "not_found";
    return true;
  });

  if (!visible.length) {
    list.innerHTML = `<p class="empty">No books match this filter.</p>`;
    return;
  }

  list.innerHTML = visible.map(bookCard).join("");
}

function bookCard(book) {
  const coverHtml = book.cover_url
    ? `<img class="book-cover" src="${esc(book.cover_url)}" alt="${esc(book.title)}" loading="lazy" />`
    : `<div class="book-cover-placeholder">📚</div>`;

  const libraryHtml = (book.library || []).map(row => {
    const badgeClass = `badge badge-${row.status}`;
    const badgeLabel = {
      available: "Available",
      unavailable: "Borrowed",
      not_found: "Not in library",
      not_at_hauptstelle: "Other branch only",
    }[row.status] ?? row.status;

    const langClass = ["Englisch","Deutsch"].includes(row.language)
      ? `lang-${row.language}`
      : "lang-other";
    const langBadge = row.language
      ? `<span class="lang-badge ${langClass}">${esc(row.language)}</span>`
      : "";

    const locationText = row.locations?.length
      ? `<span class="location-text">${row.locations.map(esc).join(" · ")}</span>`
      : "";

    const opacLink = row.url
      ? `<a class="location-link" href="${esc(row.url)}" target="_blank" rel="noopener">Catalog ↗</a>`
      : "";

    return `<div class="library-row">
      <span class="${badgeClass}">${badgeLabel}</span>
      ${langBadge}
      ${locationText}
      ${opacLink}
    </div>`;
  }).join("");

  const grLink = book.goodreads_url
    ? `<a class="gr-link" href="${esc(book.goodreads_url)}" target="_blank" rel="noopener">Goodreads ↗</a>`
    : "";

  return `<article class="book-card">
    ${coverHtml}
    <div class="book-body">
      <div class="book-title">${esc(book.title)}</div>
      <div class="book-author">${esc(book.author)}</div>
      <div class="library-rows">${libraryHtml}</div>
      ${grLink}
    </div>
  </article>`;
}

function esc(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

init();
