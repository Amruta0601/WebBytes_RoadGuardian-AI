document.addEventListener('DOMContentLoaded', () => {
    const HISTORY_KEY = 'roadguardian_alert_history_v1';
    const SETTINGS_KEY = 'roadguardian_settings_v1';
    const defaultSettings = {
        historyLimit: 200,
        highlightCritical: true,
        cctvDetectionMode: 'loose'
    };
    const appState = {
        settings: loadSettings(),
        alertHistory: loadHistory()
    };

    // 1. Initialize Map
    // Default mock location for initialization
    const defaultLat = 28.6139;
    const defaultLng = 77.2090;
    
    const map = L.map('map').setView([defaultLat, defaultLng], 13);
    let currentLat = defaultLat;
    let currentLng = defaultLng;
    
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
        subdomains: 'abcd',
        maxZoom: 20
    }).addTo(map);

    let currentMarker = L.marker([defaultLat, defaultLng]).addTo(map)
        .bindPopup('System Initialized.<br> Waiting for tracking data.')
        .openPopup();
    let locationWatchId = null;

    // 2. Initialize Socket.IO
    const socket = io();
    setupNavigation();
    hydrateSettingsUi();
    renderAllHistoryViews();
    updateAnalytics();
    fetchStatus();

    // Listen for new alerts
    socket.on('new_alert', (data) => {
        console.log('Received Alert:', data);
        addLogEntry(data.type, data.message, data.severity, data.timestamp);
        pushAlertHistory(data);
        renderAllHistoryViews();
        updateAnalytics();
        
        // Update Map if location is provided
        if (data.location && data.location.lat && data.location.lng) {
            updateMapLocation(data.location.lat, data.location.lng, data.message);
        }
        
        // Trigger visual alarm if critical
        if (data.severity === 'critical') {
            triggerSystemAlarm();
        }
    });

    // 3. Status Polling
    function fetchStatus() {
        fetch('/api/status')
            .then(response => response.json())
            .then(data => {
                updateStatusIndicator('driver-status-text', data.driver_status);
                updateStatusIndicator('cctv-status-text', data.cctv_status);
                if (data.live_location && data.live_location.lat && data.live_location.lng) {
                    updateMapLocation(data.live_location.lat, data.live_location.lng, data.live_location.address || 'Live location');
                }
                const modeHelp = document.getElementById('cctv-mode-help');
                const modeSelect = document.getElementById('cctv-detection-mode-select');
                if (modeSelect && data.cctv_detection_mode) {
                    modeSelect.value = data.cctv_detection_mode;
                    appState.settings.cctvDetectionMode = data.cctv_detection_mode;
                }
                if (modeHelp) {
                    modeHelp.textContent = data.cctv_yolo_available
                        ? 'YOLO is available. You can use Specific mode.'
                        : 'YOLO not installed. Specific mode requires: pip install ultralytics';
                    modeHelp.style.color = data.cctv_yolo_available ? 'var(--success)' : 'var(--warning)';
                }
            })
            .catch(error => console.error('Error fetching status:', error));
    }
    
    // Poll every 1 second
    setInterval(fetchStatus, 1000);

    // 4. UI Helper Functions
    function addLogEntry(type, message, severity, timestamp) {
        const logContainer = document.getElementById('event-log');
        
        const logItem = document.createElement('div');
        logItem.className = `log-item ${severity}`;
        
        let iconClass = 'fa-info-circle';
        if (severity === 'warning') iconClass = 'fa-exclamation-triangle';
        if (severity === 'critical') iconClass = 'fa-radiation';
        
        logItem.innerHTML = `
            <i class="fas ${iconClass}"></i>
            <div class="log-details">
                <span class="log-time">${timestamp}</span>
                <p><strong>[${type}]</strong> ${message}</p>
            </div>
        `;
        
        logContainer.prepend(logItem); // Add to top
    }

    function updateMapLocation(lat, lng, popupMessage) {
        const newLatLng = new L.LatLng(lat, lng);
        currentLat = lat;
        currentLng = lng;
        updateMapCoordsText();
        map.setView(newLatLng, 15);
        
        if (currentMarker) {
            map.removeLayer(currentMarker);
        }
        
        // Use a red icon for emergency
        const redIcon = new L.Icon({
            iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-2x-red.png',
            shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/0.7.7/images/marker-shadow.png',
            iconSize: [25, 41],
            iconAnchor: [12, 41],
            popupAnchor: [1, -34],
            shadowSize: [41, 41]
        });

        currentMarker = L.marker(newLatLng, {icon: redIcon}).addTo(map)
            .bindPopup(`<b>EMERGENCY ALERT</b><br>${popupMessage}`)
            .openPopup();
    }

    function updateMapCoordsText() {
        const coordsEl = document.getElementById('map-coords');
        if (coordsEl) {
            coordsEl.textContent = `Lat: ${Number(currentLat).toFixed(5)}, Lng: ${Number(currentLng).toFixed(5)}`;
        }
    }

    function updateStatusIndicator(elementId, statusText) {
        const element = document.getElementById(elementId);
        if (!element) return;
        
        element.textContent = statusText;
        
        // Reset classes
        element.className = 'status-indicator';
        
        const textLower = statusText.toLowerCase();
        if (textLower.includes('safe') || textLower.includes('normal')) {
            element.classList.add('success'); // default styling
        } else if (textLower.includes('drowsy') || textLower.includes('no driver')) {
            element.classList.add('warning');
        } else {
            element.classList.add('danger');
        }
    }

    function triggerSystemAlarm() {
        if (!appState.settings.highlightCritical) {
            return;
        }
        const overallStatus = document.getElementById('overall-status');
        overallStatus.innerHTML = '<i class="fas fa-exclamation-triangle"></i> EMERGENCY ACTIVE';
        overallStatus.className = 'status-badge danger';
        overallStatus.style.background = 'rgba(239, 68, 68, 0.2)';
        overallStatus.style.color = 'var(--danger)';
        overallStatus.style.border = '1px solid var(--danger)';
    }

    // 5. Manual SOS Trigger
    document.getElementById('manual-emergency-btn').addEventListener('click', () => {
        // Trigger a fake critical alert for demo purposes
        const now = new Date();
        const timeStr = now.getFullYear() + "-" + 
                        String(now.getMonth() + 1).padStart(2, '0') + "-" + 
                        String(now.getDate()).padStart(2, '0') + " " + 
                        String(now.getHours()).padStart(2, '0') + ":" + 
                        String(now.getMinutes()).padStart(2, '0') + ":" + 
                        String(now.getSeconds()).padStart(2, '0');
                        
        socket.emit('trigger_sos', { user: 'Admin' }); // Optional backend listener
        
        addLogEntry('MANUAL_SOS', 'Manual emergency trigger activated by user.', 'critical', timeStr);
        pushAlertHistory({
            type: 'MANUAL_SOS',
            message: 'Manual emergency trigger activated by user.',
            severity: 'critical',
            timestamp: timeStr,
            location: { lat: defaultLat, lng: defaultLng }
        });
        renderAllHistoryViews();
        updateAnalytics();
        triggerSystemAlarm();
    });

    const openMapsBtn = document.getElementById('open-maps-btn');
    if (openMapsBtn) {
        openMapsBtn.addEventListener('click', () => {
            const mapsUrl = `https://www.google.com/maps?q=${currentLat},${currentLng}`;
            window.open(mapsUrl, '_blank', 'noopener,noreferrer');
        });
    }
    updateMapCoordsText();
    startBrowserGeolocation();

    // 6. CCTV sample video upload
    const uploadButton = document.getElementById('upload-cctv-btn');
    const fileInput = document.getElementById('cctv-video-file');
    const uploadMessage = document.getElementById('upload-cctv-message');
    const cctvFeedImg = document.getElementById('cctv-feed-img');

    if (uploadButton && fileInput && uploadMessage && cctvFeedImg) {
        uploadButton.addEventListener('click', async () => {
            if (!fileInput.files || fileInput.files.length === 0) {
                uploadMessage.textContent = 'Please choose a traffic sample video first.';
                uploadMessage.style.color = 'var(--warning)';
                return;
            }

            const formData = new FormData();
            formData.append('video', fileInput.files[0]);

            uploadButton.disabled = true;
            uploadButton.textContent = 'Uploading...';
            uploadMessage.textContent = 'Uploading and switching CCTV source...';
            uploadMessage.style.color = 'var(--text-secondary)';

            try {
                const response = await fetch('/api/upload_cctv_video', {
                    method: 'POST',
                    body: formData
                });
                const data = await response.json();

                if (!response.ok || !data.ok) {
                    throw new Error(data.error || 'Upload failed');
                }

                uploadMessage.textContent = data.message;
                uploadMessage.style.color = 'var(--success)';
                cctvFeedImg.src = `/cctv_video_feed?ts=${Date.now()}`;
            } catch (error) {
                uploadMessage.textContent = `Upload failed: ${error.message}`;
                uploadMessage.style.color = 'var(--danger)';
            } finally {
                uploadButton.disabled = false;
                uploadButton.textContent = 'Upload Traffic Sample';
            }
        });
    }

    // 7. Alerts/Analytics/Settings interactions
    const clearHistoryBtn = document.getElementById('clear-history-btn');
    const exportHistoryBtn = document.getElementById('export-history-btn');
    const saveSettingsBtn = document.getElementById('save-settings-btn');

    if (clearHistoryBtn) {
        clearHistoryBtn.addEventListener('click', () => {
            appState.alertHistory = [];
            persistHistory();
            renderAllHistoryViews();
            updateAnalytics();
        });
    }

    if (exportHistoryBtn) {
        exportHistoryBtn.addEventListener('click', () => {
            const blob = new Blob([JSON.stringify(appState.alertHistory, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const anchor = document.createElement('a');
            anchor.href = url;
            anchor.download = `roadguardian-alert-history-${Date.now()}.json`;
            anchor.click();
            URL.revokeObjectURL(url);
        });
    }

    if (saveSettingsBtn) {
        saveSettingsBtn.addEventListener('click', () => {
            const historyLimitInput = document.getElementById('history-limit-input');
            const highlightCriticalToggle = document.getElementById('critical-highlight-toggle');
            const settingsMessage = document.getElementById('settings-message');
            const cctvModeSelect = document.getElementById('cctv-detection-mode-select');
            const parsedLimit = parseInt(historyLimitInput.value, 10);
            appState.settings.historyLimit = Number.isFinite(parsedLimit) ? Math.max(20, Math.min(parsedLimit, 1000)) : defaultSettings.historyLimit;
            appState.settings.highlightCritical = !!highlightCriticalToggle.checked;
            appState.settings.cctvDetectionMode = cctvModeSelect ? cctvModeSelect.value : 'loose';

            fetch('/api/cctv_detection_mode', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ mode: appState.settings.cctvDetectionMode })
            })
                .then(async (res) => {
                    const payload = await res.json();
                    if (!res.ok || !payload.ok) {
                        throw new Error(payload.message || 'Failed to update CCTV detection mode');
                    }
                    appState.settings.cctvDetectionMode = payload.mode || appState.settings.cctvDetectionMode;
                    persistSettings();
                    trimHistoryToLimit();
                    persistHistory();
                    renderAllHistoryViews();
                    updateAnalytics();
                    fetchStatus();
                    if (settingsMessage) {
                        settingsMessage.textContent = `Settings saved. ${payload.message}`;
                        settingsMessage.style.color = 'var(--success)';
                    }
                    const cctvFeedImg = document.getElementById('cctv-feed-img');
                    if (cctvFeedImg) {
                        cctvFeedImg.src = `/cctv_video_feed?ts=${Date.now()}`;
                    }
                })
                .catch((err) => {
                    if (settingsMessage) {
                        settingsMessage.textContent = `Settings save failed: ${err.message}`;
                        settingsMessage.style.color = 'var(--danger)';
                    }
                });
        });
    }

    function setupNavigation() {
        const navItems = document.querySelectorAll('.nav-item');
        const contentViews = document.querySelectorAll('.content-view');
        navItems.forEach((item) => {
            item.addEventListener('click', () => {
                const targetView = item.getAttribute('data-view');
                navItems.forEach((nav) => nav.classList.remove('active'));
                item.classList.add('active');
                contentViews.forEach((view) => {
                    view.classList.toggle('active', view.id === `view-${targetView}`);
                });
            });
        });
    }

    function loadHistory() {
        try {
            const raw = localStorage.getItem(HISTORY_KEY);
            if (!raw) return [];
            const parsed = JSON.parse(raw);
            return Array.isArray(parsed) ? parsed : [];
        } catch {
            return [];
        }
    }

    function persistHistory() {
        localStorage.setItem(HISTORY_KEY, JSON.stringify(appState.alertHistory));
    }

    function pushAlertHistory(alert) {
        appState.alertHistory.unshift({
            type: alert.type || 'SYSTEM',
            message: alert.message || 'No message provided.',
            severity: (alert.severity || 'info').toLowerCase(),
            timestamp: alert.timestamp || new Date().toISOString(),
            location: alert.location || null
        });
        trimHistoryToLimit();
        persistHistory();
    }

    function trimHistoryToLimit() {
        if (appState.alertHistory.length > appState.settings.historyLimit) {
            appState.alertHistory = appState.alertHistory.slice(0, appState.settings.historyLimit);
        }
    }

    function loadSettings() {
        try {
            const raw = localStorage.getItem(SETTINGS_KEY);
            if (!raw) return { ...defaultSettings };
            return { ...defaultSettings, ...JSON.parse(raw) };
        } catch {
            return { ...defaultSettings };
        }
    }

    function persistSettings() {
        localStorage.setItem(SETTINGS_KEY, JSON.stringify(appState.settings));
    }

    function hydrateSettingsUi() {
        const historyLimitInput = document.getElementById('history-limit-input');
        const highlightCriticalToggle = document.getElementById('critical-highlight-toggle');
        const cctvModeSelect = document.getElementById('cctv-detection-mode-select');
        if (historyLimitInput) historyLimitInput.value = String(appState.settings.historyLimit);
        if (highlightCriticalToggle) highlightCriticalToggle.checked = !!appState.settings.highlightCritical;
        if (cctvModeSelect) cctvModeSelect.value = appState.settings.cctvDetectionMode || 'loose';
    }

    function renderAllHistoryViews() {
        renderHistoryList('alerts-history-list', appState.alertHistory);
        renderHistoryList('analytics-recent-list', appState.alertHistory.slice(0, 10));
    }

    function renderHistoryList(containerId, items) {
        const container = document.getElementById(containerId);
        if (!container) return;
        if (!items.length) {
            container.innerHTML = '<div class="history-row"><div><strong>No history yet</strong><small>Incoming alerts will appear here.</small></div></div>';
            return;
        }
        container.innerHTML = items.map((item) => {
            const safeSeverity = (item.severity || 'info').toLowerCase();
            const locationText = item.location && item.location.address ? ` | ${item.location.address}` : '';
            return `
                <div class="history-row">
                    <div>
                        <strong>[${item.type}] ${item.message}</strong>
                        <small>${item.timestamp}${locationText}</small>
                    </div>
                    <span class="history-severity ${safeSeverity}">${safeSeverity}</span>
                </div>
            `;
        }).join('');
    }

    function updateAnalytics() {
        const total = appState.alertHistory.length;
        const critical = appState.alertHistory.filter((a) => a.severity === 'critical').length;
        const drowsy = appState.alertHistory.filter((a) => a.type === 'DROWSINESS').length;
        const cctv = appState.alertHistory.filter((a) => a.type === 'CCTV_CRASH').length;
        setMetric('metric-total-alerts', total);
        setMetric('metric-critical-alerts', critical);
        setMetric('metric-drowsy-alerts', drowsy);
        setMetric('metric-cctv-alerts', cctv);
    }

    function setMetric(id, value) {
        const el = document.getElementById(id);
        if (el) {
            el.textContent = String(value);
        }
    }

    function startBrowserGeolocation() {
        if (!('geolocation' in navigator)) {
            console.warn('Geolocation API unavailable in this browser.');
            return;
        }

        const onPosition = (pos) => {
            const lat = pos.coords.latitude;
            const lng = pos.coords.longitude;
            updateMapLocation(lat, lng, 'Live Browser GPS');
            fetch('/api/location/update', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    lat,
                    lng,
                    address: 'Live Browser GPS'
                })
            }).catch((err) => console.error('Location sync failed:', err));
        };

        const onError = (err) => {
            console.warn('Geolocation error:', err.message);
        };

        const options = {
            enableHighAccuracy: true,
            maximumAge: 5000,
            timeout: 10000
        };

        // initial single-shot
        navigator.geolocation.getCurrentPosition(onPosition, onError, options);
        // then continuous updates
        locationWatchId = navigator.geolocation.watchPosition(onPosition, onError, options);
    }
});

