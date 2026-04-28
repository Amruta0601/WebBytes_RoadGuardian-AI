document.addEventListener('DOMContentLoaded', () => {
    // 1. Initialize Map
    // Default mock location for initialization
    const defaultLat = 28.6139;
    const defaultLng = 77.2090;
    
    const map = L.map('map').setView([defaultLat, defaultLng], 13);
    
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
        subdomains: 'abcd',
        maxZoom: 20
    }).addTo(map);

    let currentMarker = L.marker([defaultLat, defaultLng]).addTo(map)
        .bindPopup('System Initialized.<br> Waiting for tracking data.')
        .openPopup();

    // 2. Initialize Socket.IO
    const socket = io();

    // Listen for new alerts
    socket.on('new_alert', (data) => {
        console.log('Received Alert:', data);
        addLogEntry(data.type, data.message, data.severity, data.timestamp);
        
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
        triggerSystemAlarm();
    });
});
