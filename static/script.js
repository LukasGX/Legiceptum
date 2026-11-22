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
		searchInput &&
		event.key === "/" &&
		!["INPUT", "TEXTAREA"].includes(document.activeElement.tagName)
	) {
		event.preventDefault();
		searchInput.focus();
	}

	if (
		searchInput &&
		event.key === "Escape" &&
		document.activeElement === searchInput
	) {
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

		if (!data.bookmarked) document.dispatchEvent(new Event("bookmark-removed"));
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

// modal (confirm / text input)
function showModal({
	title,
	message = "",
	confirmLabel = "OK",
	cancelLabel = "Abbrechen",
	input = null,
	danger = false
}) {
	return new Promise((resolve) => {
		const previouslyFocused = document.activeElement;
		const backdrop = document.createElement("div");
		backdrop.className = "modal-backdrop";
		backdrop.innerHTML = `
			<div class="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title">
				<h2 id="modal-title">${escapeHTML(title)}</h2>
				${message ? `<p>${escapeHTML(message)}</p>` : ""}
				${
					input
						? `<input type="text" class="modal-input" maxlength="${input.maxLength ?? 50}"
							placeholder="${escapeHTML(input.placeholder ?? "")}"
							value="${escapeHTML(input.value ?? "")}" />
						   <span class="modal-error" role="alert"></span>`
						: ""
				}
				<div class="modal-actions">
					<button type="button" class="modal-cancel">${escapeHTML(cancelLabel)}</button>
					<button type="button" class="modal-confirm ${danger ? "danger" : ""}">${escapeHTML(confirmLabel)}</button>
				</div>
			</div>
		`;

		document.body.appendChild(backdrop);

		const field = backdrop.querySelector(".modal-input");
		const error = backdrop.querySelector(".modal-error");
		const confirmBtn = backdrop.querySelector(".modal-confirm");
		const cancelBtn = backdrop.querySelector(".modal-cancel");

		(field || confirmBtn).focus();
		field?.select();

		function close(result) {
			document.removeEventListener("keydown", onKey, true);
			backdrop.remove();
			previouslyFocused?.focus?.();
			resolve(result);
		}

		function submit() {
			if (!field) return close(true);

			const value = field.value.trim();

			if (!value) {
				error.textContent = "Bitte gib einen Namen ein.";
				return;
			}

			close(value);
		}

		function onKey(event) {
			if (event.key === "Escape") {
				event.stopPropagation();
				close(null);
			} else if (event.key === "Enter" && document.activeElement !== cancelBtn) {
				event.preventDefault();
				submit();
			} else if (event.key === "Tab") {
				const focusable = [...backdrop.querySelectorAll("input, button")];
				const first = focusable[0];
				const last = focusable[focusable.length - 1];

				if (event.shiftKey && document.activeElement === first) {
					event.preventDefault();
					last.focus();
				} else if (!event.shiftKey && document.activeElement === last) {
					event.preventDefault();
					first.focus();
				}
			}
		}

		document.addEventListener("keydown", onKey, true);
		backdrop.addEventListener("mousedown", (event) => {
			if (event.target === backdrop) close(null);
		});
		cancelBtn.addEventListener("click", () => close(null));
		confirmBtn.addEventListener("click", submit);
	});
}

// collections
async function collectionRequest(url, method, body) {
	const response = await fetch(url, {
		method,
		headers: body ? { "Content-Type": "application/json" } : {},
		body: body ? JSON.stringify(body) : undefined
	});

	let data = null;
	try {
		data = await response.json();
	} catch {}

	if (!response.ok) {
		const message =
			typeof data?.detail === "string"
				? data.detail
				: "Ein Fehler ist aufgetreten";
		throw new Error(message);
	}

	return data;
}

async function promptCollectionName(title, value = "") {
	return showModal({
		title,
		confirmLabel: "Speichern",
		input: { placeholder: "Name der Sammlung", value, maxLength: 50 }
	});
}

// collections: bookmarks page
const collectionBar = document.getElementById("collection-bar");

if (collectionBar) {
	const reload = (url) => (window.location.href = url);

	const showError = async (error) =>
		showModal({
			title: "Fehler",
			message: error.message,
			confirmLabel: "OK",
			cancelLabel: "Schließen"
		});

	document
		.getElementById("collection-new")
		?.addEventListener("click", async () => {
			const name = await promptCollectionName("Neue Sammlung");
			if (!name) return;

			try {
				const created = await collectionRequest(
					"/collections",
					"POST",
					{ name }
				);
				reload(`/bookmarks?collection=${created.id}`);
			} catch (error) {
				showError(error);
			}
		});

	document
		.getElementById("collection-rename")
		?.addEventListener("click", async (event) => {
			const { id, name } = event.currentTarget.dataset;
			const newName = await promptCollectionName(
				"Sammlung umbenennen",
				name
			);
			if (!newName || newName === name) return;

			try {
				await collectionRequest(`/collections/${id}`, "PATCH", {
					name: newName
				});
				reload(`/bookmarks?collection=${id}`);
			} catch (error) {
				showError(error);
			}
		});

	document
		.getElementById("collection-delete")
		?.addEventListener("click", async (event) => {
			const { id, name } = event.currentTarget.dataset;
			const ok = await showModal({
				title: "Sammlung löschen?",
				message: `Die Sammlung „${name}“ wird gelöscht. Deine Lesezeichen bleiben erhalten.`,
				confirmLabel: "Löschen",
				danger: true
			});
			if (!ok) return;

			try {
				await collectionRequest(`/collections/${id}`, "DELETE");
				reload("/bookmarks");
			} catch (error) {
				showError(error);
			}
		});
}

// collections: law page popover
const collectionBtn = document.getElementById("collection-btn");
const collectionPopover = document.getElementById("collection-popover");

if (collectionBtn && collectionPopover) {
	const listEl = collectionPopover.querySelector(".collection-list");
	const statusEl = collectionPopover.querySelector(".collection-status");
	const newBtn = collectionPopover.querySelector(".collection-add");

	let state = { collections: [], member_of: [] };

	function setStatus(text) {
		statusEl.textContent = text || "";
	}

	function renderPopover() {
		if (state.collections.length === 0) {
			listEl.innerHTML = `<p class="collection-empty">Noch keine Sammlungen.</p>`;
			return;
		}

		listEl.innerHTML = state.collections
			.map(
				(c) => `
				<label class="collection-option">
					<input type="checkbox" value="${c.id}"
						${state.member_of.includes(c.id) ? "checked" : ""} />
					<span>${escapeHTML(c.name)}</span>
				</label>`
			)
			.join("");
	}

	async function loadState() {
		try {
			state = await collectionRequest(
				`/collections/for-law/${LAW_ID}`,
				"GET"
			);
			renderPopover();
		} catch (error) {
			setStatus(error.message);
		}
	}

	function setOpen(open) {
		collectionPopover.hidden = !open;
		collectionBtn.setAttribute("aria-expanded", open);
		if (open) {
			setStatus("");
			loadState();
		}
	}

	collectionBtn.addEventListener("click", (event) => {
		event.stopPropagation();
		setOpen(collectionPopover.hidden);
	});

	document.addEventListener("click", (event) => {
		if (
			!collectionPopover.hidden &&
			!collectionPopover.contains(event.target) &&
			!document.querySelector(".modal-backdrop")
		) {
			setOpen(false);
		}
	});

	document.addEventListener("keydown", (event) => {
		if (event.key === "Escape" && !collectionPopover.hidden) {
			setOpen(false);
			collectionBtn.focus();
		}
	});

	listEl.addEventListener("change", async (event) => {
		const box = event.target;
		if (!(box instanceof HTMLInputElement)) return;

		const id = Number(box.value);

		try {
			await collectionRequest(
				`/collections/${id}/items/${LAW_ID}`,
				box.checked ? "PUT" : "DELETE"
			);

			if (box.checked) {
				state.member_of.push(id);
				// Adding to a collection automatically bookmarks the norm
				bookmarkIcon?.classList.add("fa-solid");
				bookmarkIcon?.classList.remove("fa-regular");
			} else {
				state.member_of = state.member_of.filter((x) => x !== id);
			}
			setStatus("");
		} catch (error) {
			box.checked = !box.checked;
			setStatus(error.message);
		}
	});

	newBtn.addEventListener("click", async () => {
		const name = await promptCollectionName("Neue Sammlung");
		if (!name) return;

		try {
			const created = await collectionRequest(
				"/collections",
				"POST",
				{ name }
			);
			await collectionRequest(
				`/collections/${created.id}/items/${LAW_ID}`,
				"PUT"
			);
			bookmarkIcon?.classList.add("fa-solid");
			bookmarkIcon?.classList.remove("fa-regular");
			await loadState();
		} catch (error) {
			setStatus(error.message);
		}
	});

	// Bookmark removed -> memberships were deleted server-side
	document.addEventListener("bookmark-removed", () => {
		state.member_of = [];
		renderPopover();
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

if (globSearchForm && globSearchInput && globSearchResults) {
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
}
