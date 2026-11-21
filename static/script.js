// search
const searchInput = document.querySelector("#law-search");

if (searchInput) {
	const paragraphs = document.querySelectorAll(".paragraph-split");
	const resultsLabel = document.querySelector("#search-results");

	searchInput.addEventListener("input", () => {
		const query = searchInput.value.trim().toLowerCase();

		let count = 0;

		paragraphs.forEach((paragraph) => {
			const searchData = paragraph.dataset.search.toLowerCase();
			const matches = searchData.includes(query);

			paragraph.hidden = !matches;

			if (matches) count++;
		});

		if (resultsLabel) {
			resultsLabel.textContent = query ? `${count} Treffer` : "";
		}
	});
}

document.addEventListener("keydown", (event) => {
	if (
		event.key === "/" &&
		!["INPUT", "TEXTAREA"].includes(document.activeElement.tagName)
	) {
		event.preventDefault();
		searchInput.focus();
	}

	if (event.key === "Escape" && document.activeElement === searchInput) {
		searchInput.value = "";
		searchInput.dispatchEvent(new Event("input"));
		searchInput.blur();
	}
});

// responsive
const toggle = document.getElementById("nav-toggle");
const nav = document.querySelector("aside.nav");

toggle.addEventListener("click", () => {
	const open = nav.classList.toggle("open");

	toggle.innerHTML = open
		? '<i class="fa-solid fa-xmark"></i>'
		: '<i class="fa-solid fa-bars"></i>';

	toggle.setAttribute("aria-expanded", open);
});

// bookmark icon
const bookmarkIcon = document.getElementById("bookmark-icon");

if (bookmarkIcon) {
	bookmarkIcon.addEventListener("click", async () => {
		const response = await fetch(`/bookmark/${LAW_ID}`, {
			method: "POST"
		});

		const data = await response.json();

		bookmarkIcon.classList.toggle("fa-solid", data.bookmarked);
		bookmarkIcon.classList.toggle("fa-regular", !data.bookmarked);
	});
}

// notes
const saveNotesBtn = document.getElementById("save-notes");
const saveFeedback = document.getElementById("save-feedback");
const notesEl = document.getElementById("notes");
const charCounter = document.getElementById("char-counter");

if (saveNotesBtn) {
	saveNotesBtn.addEventListener("click", async () => {
		const response = await fetch(`/notes/${LAW_ID}`, {
			method: "POST",
			headers: {
				"Content-Type": "application/json"
			},
			body: JSON.stringify({
				content: notesEl.value
			})
		});

		const data = await response.json();

		if (saveFeedback) {
			if (response.ok) saveFeedback.textContent = "Notizen gespeichert";
			else saveFeedback.textContent = "Ein Fehler ist aufgetreten";

			setTimeout(() => {
				saveFeedback.textContent = "";
			}, 1500);
		}
	});
}

if (notesEl) {
	notesEl.addEventListener("input", () => {
		if (charCounter)
			charCounter.textContent = `${notesEl.value.length} / 2000`;
	});
}

// global search
const globSearchForm = document.getElementById("global-search-form");
const globSearchInput = document.getElementById("global-search-input");
const globSearchResults = document.getElementById("search-results");

let currentQuery = "";
let currentScopes = [];
let currentPage = 1;
let hasMore = false;
let isLoading = false;

const SEARCH_LIMIT = 150;

// HTML-sicheres Einfügen
function escapeHTML(value) {
	return String(value ?? "").replace(
		/[&<>"']/g,
		(char) =>
			({
				"&": "&amp;",
				"<": "&lt;",
				">": "&gt;",
				'"': "&quot;",
				"'": "&#039;"
			})[char]
	);
}

// Suchbegriff in Text hervorheben
function highlightText(text, query) {
	const safeText = escapeHTML(text);
	const safeQuery = escapeHTML(query);

	if (!safeQuery) return safeText;

	const escapedQuery = safeQuery.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

	return safeText.replace(
		new RegExp(`(${escapedQuery})`, "gi"),
		"<mark>$1</mark>"
	);
}

function renderBook(book) {
	return `
        <a href="/book/${encodeURIComponent(book.id)}" class="search-result">
            <div class="search-result-content">
                <span class="search-result-title">
                    ${escapeHTML(book.name)}
                </span>

                <div class="search-result-meta">
                    <span>${escapeHTML(book.abbr)}</span>
                    <span>·</span>
                    <span>${escapeHTML(book.scope)}</span>
                </div>
            </div>
        </a>
    `;
}

function renderLaw(law) {
	const title =
		law.prefix && !law.no_prefix
			? `${law.prefix} ${law.nr} - ${law.name}`
			: `${law.prefix || ""}${law.nr} - ${law.name}`;

	const snippet = law.snippet
		? `<p class="search-result-snippet">
                ${highlightText(law.snippet, currentQuery)}
           </p>`
		: "";

	return `
        <a href="/law/${encodeURIComponent(law.id)}" class="search-result">
            <div class="search-result-content">
                <span class="search-result-title">
                    ${escapeHTML(title)}
                </span>

                <div class="search-result-meta">
                    <span>${escapeHTML(law.book.abbr)}</span>
                    <span>·</span>
                    <span>${escapeHTML(law.book.name)}</span>
                </div>

                ${snippet}
            </div>
        </a>
    `;
}

function renderResults(data, append = false) {
	const books = data.results.books;
	const laws = data.results.laws;

	let html = "";

	if (!append) {
		html += `
            <p class="search-result-count">
                ${data.total} Treffer für
                <strong>${escapeHTML(data.query)}</strong>
            </p>
        `;
	}

	if (books.length > 0) {
		html += `
            <div class="search-result-group">
                <h2>Gesetzbücher (${books.length})</h2>
                ${books.map(renderBook).join("")}
            </div>
        `;
	}

	if (laws.length > 0) {
		html += `
            <div class="search-result-group">
                <h2>Einzelnormen (${laws.length})</h2>
                ${laws.map(renderLaw).join("")}
            </div>
        `;
	}

	if (!append && books.length === 0 && laws.length === 0) {
		html += `
            <p class="search-empty">
                Keine Suchergebnisse gefunden.
            </p>
        `;
	}

	if (append) {
		const oldButton = globSearchResults.querySelector("#load-more");
		oldButton?.remove();

		globSearchResults.insertAdjacentHTML("beforeend", html);
	} else {
		globSearchResults.innerHTML = html;
	}

	hasMore = data.has_more;

	if (hasMore) {
		globSearchResults.insertAdjacentHTML(
			"beforeend",
			`
            <div class="search-load-more">
                <button type="button" id="load-more">
                    Weitere Ergebnisse laden
                </button>
            </div>
        `
		);

		document
			.getElementById("load-more")
			.addEventListener("click", () => loadMoreResults());
	}
}

async function performSearch(page = 1, append = false) {
	if (isLoading) return;

	isLoading = true;

	if (!append) {
		globSearchResults.innerHTML = `
            <p class="search-loading">Suche läuft ...</p>
        `;
	} else {
		const button = document.getElementById("load-more");
		if (button) {
			button.disabled = true;
			button.textContent = "Lädt ...";
		}
	}

	try {
		const response = await fetch("/search", {
			method: "POST",
			headers: {
				"Content-Type": "application/json"
			},
			body: JSON.stringify({
				query: currentQuery,
				scope: currentScopes,
				page: page,
				limit: SEARCH_LIMIT
			})
		});

		if (!response.ok) {
			throw new Error(`HTTP ${response.status}`);
		}

		const data = await response.json();

		currentPage = data.page;

		renderResults(data, append);
	} catch (error) {
		console.error("Fehler bei der Suche:", error);

		if (!append) {
			globSearchResults.innerHTML = `
                <p class="search-error">
                    Bei der Suche ist ein Fehler aufgetreten.
                    Bitte versuche es erneut.
                </p>
            `;
		} else {
			const button = document.getElementById("load-more");
			if (button) {
				button.disabled = false;
				button.textContent = "Weitere Ergebnisse laden";
			}
		}
	} finally {
		isLoading = false;
	}
}

function loadMoreResults() {
	if (!hasMore || isLoading) return;

	performSearch(currentPage + 1, true);
}

globSearchForm.addEventListener("submit", (event) => {
	event.preventDefault();

	currentQuery = globSearchInput.value.trim();

	currentScopes = Array.from(
		globSearchForm.querySelectorAll('input[name="scope"]:checked')
	).map((input) => input.value);

	if (!currentQuery) return;

	if (currentScopes.length === 0) {
		globSearchResults.innerHTML = `
            <p class="search-error">
                Bitte wähle mindestens einen Suchbereich aus.
            </p>
        `;
		return;
	}

	currentPage = 1;
	hasMore = false;

	performSearch(1);
});
