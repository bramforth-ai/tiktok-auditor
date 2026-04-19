/* ================================================================
   TikTok Auditor — Client-side JavaScript
   ================================================================ */

// ============================================================
// Toast Notifications
// ============================================================

function notify(message, type = 'success') {
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.textContent = message;
    document.body.appendChild(el);
    setTimeout(() => {
        el.style.transition = 'opacity 0.3s';
        el.style.opacity = '0';
        setTimeout(() => el.remove(), 300);
    }, 3500);
}


// ============================================================
// Scan Progress Helpers
// ============================================================

function showScanProgress(containerId) {
    const el = document.getElementById(containerId);
    if (el) el.classList.add('active');
}

function hideScanProgress(containerId) {
    const el = document.getElementById(containerId);
    if (el) el.classList.remove('active');
}

function setScanCount(countElId, val) {
    const el = document.getElementById(countElId);
    if (el) el.textContent = val;
}

function setScanText(textElId, text) {
    const el = document.getElementById(textElId);
    if (el) el.textContent = text;
}


// ============================================================
// Scanning — Own Channel (first-time setup)
// ============================================================

async function setupOwnChannel() {
    const username = document.getElementById('own-username').value.trim().replace('@', '');
    if (!username) { notify('Enter your username', 'error'); return; }

    const dateFrom = document.getElementById('own-date-from')?.value?.replace(/-/g, '') || '';
    const dateTo = document.getElementById('own-date-to')?.value?.replace(/-/g, '') || '';
    const maxVideos = document.getElementById('own-max-videos')?.value || '';

    const btn = document.getElementById('own-scan-btn');
    btn.disabled = true;
    btn.textContent = 'Scanning…';

    showScanProgress('own-scan-progress');
    setScanText('own-scan-text', 'Starting scan of @' + username + '…');
    setScanCount('own-scan-count', '0');

    try {
        const resp = await fetch('/api/scan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                username,
                date_from: dateFrom || null,
                date_to: dateTo || null,
                max_videos: maxVideos || null,
                is_own: true,
            }),
        });
        const data = await resp.json();

        if (data.success) {
            pollScanProgress(username, {
                containerId: 'own-scan-progress',
                textId: 'own-scan-text',
                countId: 'own-scan-count',
                redirect: true,
            });
        } else {
            notify(data.error || 'Scan failed', 'error');
            btn.disabled = false;
            btn.textContent = 'Scan My Channel';
            hideScanProgress('own-scan-progress');
        }
    } catch (e) {
        notify('Scan failed: ' + e.message, 'error');
        btn.disabled = false;
        btn.textContent = 'Scan My Channel';
        hideScanProgress('own-scan-progress');
    }
}


// ============================================================
// Scanning — Own Channel (rescan from index page)
// ============================================================

async function rescanOwn(username) {
    const dateFrom = document.getElementById('rescan-date-from')?.value?.replace(/-/g, '') || '';
    const dateTo = document.getElementById('rescan-date-to')?.value?.replace(/-/g, '') || '';
    const maxVideos = document.getElementById('rescan-max-videos')?.value || '';

    const btn = document.getElementById('rescan-btn');
    btn.disabled = true;
    btn.textContent = 'Scanning…';

    showScanProgress('rescan-scan-progress');
    setScanText('rescan-scan-text', 'Scanning…');
    setScanCount('rescan-scan-count', '0');

    try {
        const resp = await fetch('/api/scan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                username,
                date_from: dateFrom || null,
                date_to: dateTo || null,
                max_videos: maxVideos || null,
                is_own: true,
            }),
        });
        const data = await resp.json();

        if (data.success) {
            pollScanProgress(username, {
                containerId: 'rescan-scan-progress',
                textId: 'rescan-scan-text',
                countId: 'rescan-scan-count',
                redirect: false,
            });
        } else {
            notify(data.error || 'Scan failed', 'error');
            btn.disabled = false;
            btn.textContent = 'Re-scan';
            hideScanProgress('rescan-scan-progress');
        }
    } catch (e) {
        notify('Rescan failed: ' + e.message, 'error');
        btn.disabled = false;
        btn.textContent = 'Re-scan';
        hideScanProgress('rescan-scan-progress');
    }
}


// ============================================================
// Scanning — Change Own Channel
// ============================================================

async function changeOwnChannel() {
    const username = document.getElementById('own-username').value.trim().replace('@', '');
    if (!username) { notify('Enter a username', 'error'); return; }

    try {
        const resp = await fetch('/api/set-own-channel', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username }),
        });
        const data = await resp.json();
        if (data.success) {
            notify('Channel updated to @' + username);
            window.location.reload();
        } else {
            notify(data.error || 'Failed', 'error');
        }
    } catch (e) {
        notify('Error: ' + e.message, 'error');
    }
}

async function deleteStyleProfile(username) {
    if (!confirm('Delete your style profile for @' + username + '? You can regenerate it later.')) return;
    try {
        const resp = await fetch('/api/delete-style-profile/' + username, { method: 'POST' });
        const data = await resp.json();
        if (data.success) {
            notify('Style profile deleted');
            window.location.reload();
        } else {
            notify(data.error || 'Failed', 'error');
        }
    } catch (e) {
        notify('Error: ' + e.message, 'error');
    }
}


// ============================================================
// Delete — Reports, Competitors
// ============================================================

async function deleteReport(username, filename) {
    if (!confirm(`Delete report "${filename}"?\n\nThis cannot be undone.`)) return;
    try {
        const resp = await fetch(`/api/report/${username}/${encodeURIComponent(filename)}/delete`, {
            method: 'POST'
        });
        const data = await resp.json().catch(() => ({}));
        if (resp.ok && data.success) {
            notify('Report deleted');
            window.location.reload();
        } else {
            notify(data.error || 'Failed to delete', 'error');
        }
    } catch (e) {
        notify('Error: ' + e.message, 'error');
    }
}

async function deleteCompetitor(username) {
    const msg = `Delete competitor @${username} entirely?\n\n` +
                `This removes all scanned metadata, transcripts, videos, scorecards, reports, ` +
                `and any rewritten scripts you have for this channel. This cannot be undone.\n\n` +
                `Type the username below to confirm.`;
    const typed = prompt(msg, '');
    if (typed === null) return;
    if (typed.trim().replace(/^@/, '') !== username) {
        notify('Username mismatch — deletion cancelled', 'error');
        return;
    }
    try {
        const resp = await fetch(`/api/channel/${username}/delete`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ confirm_username: username }),
        });
        const data = await resp.json().catch(() => ({}));
        if (resp.ok && data.success) {
            notify(`@${username} deleted`);
            window.location.reload();
        } else {
            notify(data.error || 'Failed to delete', 'error');
        }
    } catch (e) {
        notify('Error: ' + e.message, 'error');
    }
}


// ============================================================
// Scanning — Competitor
// ============================================================

async function scanCompetitor() {
    const username = document.getElementById('comp-username').value.trim().replace('@', '');
    if (!username) { notify('Enter a competitor username', 'error'); return; }

    const dateFrom = document.getElementById('comp-date-from')?.value?.replace(/-/g, '') || '';
    const dateTo = document.getElementById('comp-date-to')?.value?.replace(/-/g, '') || '';
    const maxVideos = document.getElementById('comp-max-videos')?.value || '';

    const btn = document.getElementById('comp-scan-btn');
    btn.disabled = true;
    btn.textContent = 'Scanning…';

    showScanProgress('comp-scan-progress');
    setScanText('comp-scan-text', 'Starting scan of @' + username + '…');
    setScanCount('comp-scan-count', '0');

    try {
        const resp = await fetch('/api/scan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                username,
                date_from: dateFrom || null,
                date_to: dateTo || null,
                max_videos: maxVideos || null,
                is_own: false,
            }),
        });
        const data = await resp.json();

        if (data.success) {
            pollScanProgress(username, {
                containerId: 'comp-scan-progress',
                textId: 'comp-scan-text',
                countId: 'comp-scan-count',
                redirect: true,
            });
        } else {
            notify(data.error || 'Scan failed', 'error');
            btn.disabled = false;
            btn.textContent = 'Scan Competitor';
            hideScanProgress('comp-scan-progress');
        }
    } catch (e) {
        notify('Scan failed: ' + e.message, 'error');
        btn.disabled = false;
        btn.textContent = 'Scan Competitor';
        hideScanProgress('comp-scan-progress');
    }
}


// ============================================================
// Scanning — Shared Poll Function
// ============================================================

function pollScanProgress(username, opts) {
    const { containerId, textId, countId, redirect } = opts;

    const interval = setInterval(async () => {
        try {
            const resp = await fetch('/api/scan/status');
            const data = await resp.json();

            if (data.is_scanning) {
                setScanText(textId, `Scanning @${username}… Found ${data.videos_found} videos so far`);
            } else if (data.finished) {
                setScanText(textId, `Scan complete — ${data.videos_found} videos found`);
            }

            if (countId) setScanCount(countId, data.videos_found);

            if (data.finished) {
                clearInterval(interval);
                if (data.error) {
                    notify('Scan error: ' + data.error, 'error');
                } else {
                    notify(`Found ${data.videos_found} videos`);
                    if (redirect) {
                        setTimeout(() => { window.location.href = `/channel/${username}`; }, 800);
                    } else {
                        setTimeout(() => window.location.reload(), 800);
                    }
                }
            }
        } catch (e) {
            console.error('Scan poll error:', e);
        }
    }, 1500);
}


// ============================================================
// Dashboard — Rescan (from dashboard page)
// ============================================================

async function rescanChannel(username) {
    const dateFrom = document.getElementById('rescan-date-from')?.value?.replace(/-/g, '') || '';
    const dateTo = document.getElementById('rescan-date-to')?.value?.replace(/-/g, '') || '';
    const maxVideos = document.getElementById('rescan-max-videos')?.value || '';

    const btn = document.getElementById('rescan-btn');
    btn.disabled = true;
    btn.textContent = 'Scanning…';

    showScanProgress('rescan-progress');
    setScanText('rescan-progress-text', 'Scanning…');

    try {
        const resp = await fetch('/api/scan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                username,
                date_from: dateFrom || null,
                date_to: dateTo || null,
                max_videos: maxVideos || null,
            }),
        });
        const data = await resp.json();

        if (data.success) {
            pollScanProgress(username, {
                containerId: 'rescan-progress',
                textId: 'rescan-progress-text',
                countId: 'rescan-scan-count',
                redirect: false,
            });
        } else {
            notify(data.error || 'Scan failed', 'error');
            btn.disabled = false;
            btn.textContent = 'Re-scan';
            hideScanProgress('rescan-progress');
        }
    } catch (e) {
        notify('Rescan failed: ' + e.message, 'error');
        btn.disabled = false;
        btn.textContent = 'Re-scan';
        hideScanProgress('rescan-progress');
    }
}


// ============================================================
// Video Table — Selection
// ============================================================

function updateSelectedCount() {
    const checked = document.querySelectorAll('.video-checkbox:checked');
    const countEl = document.getElementById('selected-count');
    if (countEl) countEl.textContent = `${checked.length} selected`;

    const processBtn = document.getElementById('process-btn');
    if (processBtn) processBtn.disabled = checked.length === 0;
}

function selectAll() {
    document.querySelectorAll('.video-checkbox').forEach(cb => cb.checked = true);
    updateSelectedCount();
}

function selectNone() {
    document.querySelectorAll('.video-checkbox').forEach(cb => cb.checked = false);
    updateSelectedCount();
}

function selectTop(n) {
    selectNone();
    let count = 0;
    const checkboxes = document.querySelectorAll('.video-checkbox');
    for (const cb of checkboxes) {
        if (count >= n) break;
        const status = cb.closest('tr')?.dataset.status;
        if (!status || status === 'unprocessed' || status === 'failed') {
            cb.checked = true;
            count++;
        }
    }
    if (count === 0) {
        notify('No unprocessed videos remaining', 'error');
    } else if (count < n) {
        notify(`Selected ${count} unprocessed (only ${count} remaining)`);
    }
    updateSelectedCount();
}

function selectUnprocessed() {
    selectNone();
    document.querySelectorAll('.video-checkbox').forEach(cb => {
        const row = cb.closest('tr');
        const status = row?.dataset.status;
        if (!status || status === 'unprocessed' || status === 'failed') {
            cb.checked = true;
        }
    });
    updateSelectedCount();
}

function getSelectedVideoIds() {
    return Array.from(document.querySelectorAll('.video-checkbox:checked')).map(cb => cb.value);
}


// ============================================================
// Video Table — Sorting
// ============================================================

let currentSort = { column: 'engagement_rate', direction: 'desc' };

function sortTable(column) {
    const table = document.getElementById('video-table');
    if (!table) return;

    const tbody = table.querySelector('tbody');
    const rows = Array.from(tbody.querySelectorAll('tr'));

    if (currentSort.column === column) {
        currentSort.direction = currentSort.direction === 'asc' ? 'desc' : 'asc';
    } else {
        currentSort.column = column;
        currentSort.direction = 'desc';
    }

    // Update header indicators
    document.querySelectorAll('th[data-sort]').forEach(th => {
        th.classList.remove('sorted-asc', 'sorted-desc');
    });
    const activeHeader = document.querySelector(`th[data-sort="${column}"]`);
    if (activeHeader) {
        activeHeader.classList.add(currentSort.direction === 'asc' ? 'sorted-asc' : 'sorted-desc');
    }

    const numericCols = ['view_count', 'like_count', 'comment_count', 'repost_count',
                         'save_count', 'engagement_rate', 'duration'];

    rows.sort((a, b) => {
        let aVal = a.dataset[column] || '';
        let bVal = b.dataset[column] || '';

        if (numericCols.includes(column)) {
            aVal = parseFloat(aVal) || 0;
            bVal = parseFloat(bVal) || 0;
        }

        if (aVal < bVal) return currentSort.direction === 'asc' ? -1 : 1;
        if (aVal > bVal) return currentSort.direction === 'asc' ? 1 : -1;
        return 0;
    });

    rows.forEach(row => tbody.appendChild(row));
    renderPage(); // re-paginate after sort
}


// ============================================================
// Video Table — Pagination
// ============================================================

let currentPage = 1;
let pageSize = 100;
let unprocessedOnly = false;  // row-visibility filter toggle

function getAllRows() {
    const table = document.getElementById('video-table');
    if (!table) return [];
    return Array.from(table.querySelectorAll('tbody tr'));
}

function getVisibleRows() {
    // Apply the unprocessed-only filter at the row level (before pagination).
    const all = getAllRows();
    if (!unprocessedOnly) return all;
    return all.filter(r => {
        const s = r.dataset.status;
        return !s || s === 'unprocessed' || s === 'failed';
    });
}

function getTotalPages() {
    return Math.max(1, Math.ceil(getVisibleRows().length / pageSize));
}

function renderPage() {
    const all = getAllRows();
    const visible = getVisibleRows();
    const totalPages = getTotalPages();

    // Clamp current page
    if (currentPage > totalPages) currentPage = totalPages;
    if (currentPage < 1) currentPage = 1;

    const start = (currentPage - 1) * pageSize;
    const end = start + pageSize;

    // First: hide all rows filtered out by the unprocessed-only toggle.
    const visibleSet = new Set(visible);
    all.forEach(row => {
        if (!visibleSet.has(row)) row.style.display = 'none';
    });

    // Then paginate the visible set.
    visible.forEach((row, i) => {
        row.style.display = (i >= start && i < end) ? '' : 'none';
    });

    // Update controls
    const info = document.getElementById('page-info');
    if (info) {
        const suffix = unprocessedOnly
            ? ` · ${visible.length} of ${all.length} (filtered)`
            : ` · ${all.length} videos`;
        info.textContent = `Page ${currentPage} of ${totalPages}${suffix}`;
    }

    const prevBtn = document.getElementById('page-prev');
    const nextBtn = document.getElementById('page-next');
    if (prevBtn) prevBtn.disabled = currentPage <= 1;
    if (nextBtn) nextBtn.disabled = currentPage >= totalPages;

    // Hide pagination entirely if only 1 page
    const pag = document.getElementById('pagination');
    if (pag) pag.style.display = totalPages <= 1 ? 'none' : 'flex';
}

function toggleUnprocessedOnly() {
    unprocessedOnly = !unprocessedOnly;
    currentPage = 1;
    const btn = document.getElementById('filter-toggle-btn');
    if (btn) btn.textContent = unprocessedOnly ? 'Show all videos' : 'Show unprocessed only';
    renderPage();
}

function goToPage(page) {
    currentPage = page;
    renderPage();
    // Scroll table into view
    const table = document.getElementById('video-table');
    if (table) table.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function setPageSize(size) {
    pageSize = size;
    currentPage = 1;
    renderPage();
}


// ============================================================
// Processing
// ============================================================

let pollInterval = null;

async function startProcessing(username, mode, videoIds) {
    const ids = videoIds || getSelectedVideoIds();
    if (ids.length === 0) {
        notify('Select at least one video', 'error');
        return;
    }

    const body = { username, video_ids: ids, mode };

    // Auto-inject style profile for competitor analysis
    if (mode === 'competitor_intel' && window.ownUsername) {
        body.style_profile_username = window.ownUsername;
    }

    try {
        const resp = await fetch('/api/process', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await resp.json();

        if (data.success) {
            // Switch to processing view
            document.getElementById('table-section').style.display = 'none';
            const procPanel = document.getElementById('processing-section');
            if (procPanel) procPanel.classList.add('active');
            beginPolling();
        } else {
            notify(data.error || 'Failed to start processing', 'error');
        }
    } catch (e) {
        notify('Error: ' + e.message, 'error');
    }
}

function beginPolling() {
    pollInterval = setInterval(pollStatus, 2000);
    pollStatus(); // immediate first check
}

function stopPolling() {
    if (pollInterval) {
        clearInterval(pollInterval);
        pollInterval = null;
    }
}

async function pollStatus() {
    try {
        const resp = await fetch('/api/process/status');
        const data = await resp.json();
        renderProcessingUI(data);

        if (data.finished || (!data.is_processing && data.completed > 0)) {
            stopPolling();
        }
    } catch (e) {
        console.error('Poll error:', e);
    }
}

function renderProcessingUI(data) {
    const pct = data.total > 0 ? Math.round((data.completed / data.total) * 100) : 0;

    // Progress bar
    const fill = document.getElementById('progress-fill');
    if (fill) fill.style.width = pct + '%';

    // Progress text
    const textEl = document.getElementById('progress-text');
    if (textEl) {
        const stage = data.stage ? ` · ${data.stage}` : '';
        textEl.textContent = `Processing ${data.completed} of ${data.total}${stage}`;
    }

    const pctEl = document.getElementById('progress-pct');
    if (pctEl) pctEl.textContent = pct + '%';

    // Counters
    const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
    set('proc-scored', data.scored || 0);
    set('proc-failed', data.failed || 0);
    set('proc-triaged', data.triaged_out || 0);
    set('proc-skipped', data.no_transcript || 0);

    // Results feed
    const feed = document.getElementById('results-feed');
    if (feed && data.results) {
        feed.innerHTML = '';
        const reversed = [...data.results].reverse();
        reversed.forEach(r => {
            const row = document.createElement('div');
            row.className = 'result-row';

            let iconClass = 'skip';
            let icon = '○';
            let statusText = r.status || 'processing';

            if (r.status === 'scored' || r.status === 'rewritten') {
                icon = '✓'; iconClass = 'success';
                statusText = r.status;
            } else if (r.status === 'already_scored') {
                icon = '↺'; iconClass = 'skip';
                statusText = 'already scored — skipped';
            } else if (r.status === 'passed_triage') {
                icon = '→'; iconClass = 'pass';
                statusText = 'passed triage';
            } else if (r.status === 'failed') {
                icon = '✗'; iconClass = 'fail';
                statusText = r.error || 'failed';
            } else if (r.status === 'triaged_out') {
                icon = '⊘'; iconClass = 'triage';
                statusText = r.reason || 'filtered out';
            } else if (r.status === 'no_transcript') {
                icon = '○'; iconClass = 'skip';
                statusText = 'no transcript';
            }

            // Use video title if available, otherwise show ID
            const title = r.title || '';
            const idDisplay = r.video_id ? r.video_id.substring(0, 10) + '…' : '';

            row.innerHTML = `
                <span class="r-icon ${iconClass}">${icon}</span>
                <span class="r-id">${idDisplay}</span>
                <span class="r-title">${title}</span>
                <span class="r-status">${statusText}</span>
            `;
            feed.appendChild(row);
        });
    }

    // Show completion state
    if (data.finished) {
        const doneEl = document.getElementById('processing-finished');
        if (doneEl) doneEl.classList.add('visible');

        const cancelBtn = document.getElementById('cancel-btn');
        if (cancelBtn) cancelBtn.style.display = 'none';
    }
}

async function cancelProcessing() {
    try {
        await fetch('/api/process/cancel', { method: 'POST' });
        notify('Cancel requested — finishing current video');
    } catch (e) {
        notify('Cancel error: ' + e.message, 'error');
    }
}


// ============================================================
// Retry Failed
// ============================================================

function retryFailed() {
    const failedRows = document.querySelectorAll('tr[data-status="failed"]');
    const ids = Array.from(failedRows).map(r => r.dataset.videoId).filter(Boolean);
    if (ids.length === 0) {
        notify('No failed videos to retry', 'error');
        return;
    }
    const username = window.dashboardUsername;
    const mode = window.currentMode;
    if (username && mode) {
        startProcessing(username, mode, ids);
    }
}


// ============================================================
// Report Generation
// ============================================================

async function generateReport(username, mode) {
    const overlay = document.getElementById('report-overlay');
    if (overlay) overlay.classList.add('visible');

    const body = { username, mode };

    if (mode === 'competitor_intel' && window.ownUsername) {
        body.style_profile_username = window.ownUsername;
    }

    try {
        const resp = await fetch('/api/report/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await resp.json();

        if (data.success) {
            notify('Report generated!');
            window.location.href = `/channel/${username}/report/${data.filename}`;
        } else {
            notify(data.error || 'Report generation failed', 'error');
            if (overlay) overlay.classList.remove('visible');
        }
    } catch (e) {
        notify('Error: ' + e.message, 'error');
        if (overlay) overlay.classList.remove('visible');
    }
}


// ============================================================
// Init
// ============================================================

document.addEventListener('DOMContentLoaded', () => {
    // --- Video table interactions ---
    document.querySelectorAll('.video-checkbox').forEach(cb => {
        cb.addEventListener('change', updateSelectedCount);
    });

    // Header checkbox (select all)
    const headerCb = document.getElementById('select-all-cb');
    if (headerCb) {
        headerCb.addEventListener('change', () => {
            if (headerCb.checked) selectAll();
            else selectNone();
        });
    }

    // Sortable column headers
    document.querySelectorAll('th[data-sort]').forEach(th => {
        th.addEventListener('click', () => sortTable(th.dataset.sort));
    });

    // Default sort indicator
    const defaultHeader = document.querySelector('th[data-sort="engagement_rate"]');
    if (defaultHeader) defaultHeader.classList.add('sorted-desc');

    // Initial selected count
    updateSelectedCount();

    // Initial pagination
    renderPage();
});
