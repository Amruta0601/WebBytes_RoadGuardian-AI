document.addEventListener('DOMContentLoaded', () => {

  // ════════════════════════════════════════════════════════════════
  // 1. Core State & Elements
  // ════════════════════════════════════════════════════════════════
  const socket = io();
  let alertCount = 0;

  // DOM Elements
  const elStatusIcon = document.querySelector('#overall-status .dot');
  const elStatusText = document.getElementById('overall-status-text');
  const elUptime     = document.getElementById('uptime-clock');
  const elLog        = document.getElementById('event-log');
  const elBtnClear   = document.getElementById('btn-clear-log');
  const elCctvInput  = document.getElementById('cctv-video-input');
  const elFileLabel  = document.getElementById('file-chosen-label');
  const elBtnUpload  = document.getElementById('btn-upload-cctv');
  const elBtnSos     = document.getElementById('btn-sos');
  const elStatAlerts = document.getElementById('stat-alerts');

  // Alarm & Modal Elements
  const overlay        = document.getElementById('alarm-overlay');
  const emModal        = document.getElementById('em-modal');
  const emModalTitle   = document.getElementById('em-modal-title');
  const emModalBody    = document.getElementById('em-modal-body');
  const emModalClose   = document.getElementById('em-modal-close');
  const emModalCall    = document.getElementById('em-modal-call');
  const emModalMap     = document.getElementById('em-modal-map');

  // ════════════════════════════════════════════════════════════════
  // 2. Map Initialisation
  // ════════════════════════════════════════════════════════════════
  const defaultLat = 12.9716;
  const defaultLng = 77.5946;

  const map = L.map('map').setView([defaultLat, defaultLng], 14);
  L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
    attribution: '&copy; CARTO',
    subdomains: 'abcd',
    maxZoom: 20
  }).addTo(map);

  const redIcon = new L.Icon({
    iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-2x-red.png',
    shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/0.7.7/images/marker-shadow.png',
    iconSize: [25, 41], iconAnchor: [12, 41], popupAnchor: [1, -34], shadowSize: [41, 41]
  });

  const blueIcon = new L.Icon({
    iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-2x-blue.png',
    shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/0.7.7/images/marker-shadow.png',
    iconSize: [25, 41], iconAnchor: [12, 41], popupAnchor: [1, -34], shadowSize: [41, 41]
  });

  let vehicleMarker = L.marker([defaultLat, defaultLng], { icon: blueIcon })
    .addTo(map)
    .bindPopup('<b>Vehicle Location</b><br>System Initialised.')
    .openPopup();

  function updateMapLocation(lat, lng, label, isEmergency = false) {
    const latlng = new L.LatLng(lat, lng);
    map.setView(latlng, 15);
    vehicleMarker.setLatLng(latlng);
    vehicleMarker.setIcon(isEmergency ? redIcon : blueIcon);
    vehicleMarker.setPopupContent(`<b>${isEmergency ? 'EMERGENCY LOCATION' : 'Vehicle Location'}</b><br>${label}`);
    if (isEmergency) vehicleMarker.openPopup();
  }

  // ════════════════════════════════════════════════════════════════
  // 3. UI Helpers
  // ════════════════════════════════════════════════════════════════

  // Uptime clock
  const startTime = Date.now();
  setInterval(() => {
    const diff = Math.floor((Date.now() - startTime) / 1000);
    const h = String(Math.floor(diff / 3600)).padStart(2, '0');
    const m = String(Math.floor((diff % 3600) / 60)).padStart(2, '0');
    const s = String(diff % 60).padStart(2, '0');
    elUptime.textContent = `${h}:${m}:${s}`;
  }, 1000);

  // Initial event log time
  document.getElementById('init-time').textContent = new Date().toLocaleTimeString();

  function setOverallStatus(state) {
    // state: 'safe', 'warn', 'danger'
    const st = document.getElementById('overall-status');
    st.className = 'status-badge ' + (state === 'danger' ? 'danger' : '');

    elStatusIcon.className = 'dot ' + (state === 'danger' ? 'red-dot' : state === 'warn' ? 'orange-dot' : 'green-dot');
    elStatusText.textContent = state === 'danger' ? 'EMERGENCY ACTIVE' : state === 'warn' ? 'WARNING ISSUED' : 'All Systems Active';
  }

  function appendLog(type, msg, severity, timestamp) {
    alertCount++;
    elStatAlerts.textContent = alertCount;

    const div = document.createElement('div');
    div.className = `log-item ${severity}`;

    let icon = 'fa-circle-info';
    if (severity === 'warning' || severity === 'medium' || severity === 'high') icon = 'fa-triangle-exclamation';
    if (severity === 'critical') icon = 'fa-radiation';

    div.innerHTML = `
      <i class="fas ${icon}"></i>
      <div class="log-detail">
        <span class="log-time">${timestamp || new Date().toLocaleTimeString()}</span>
        <p><strong>[${type}]</strong> ${msg}</p>
      </div>
    `;
    elLog.prepend(div);
  }

  function triggerAlarmOverlay() {
    overlay.classList.remove('hidden');
    setOverallStatus('danger');
    setTimeout(() => {
      // Auto dismiss overlay flash after 8 seconds if not dismissed manually
      overlay.classList.add('hidden');
      if (document.getElementById('overall-status').classList.contains('danger')) {
         setOverallStatus('warn');
      }
    }, 8000);
  }

  function showEmergencyModal(data) {
    emModalTitle.textContent = `DRIVER EMERGENCY: ${data.incident_type.toUpperCase().replace('_', ' ')}`;
    emModalCall.href = `tel:${data.emergency_number}`;
    emModalMap.href = data.location.maps_link;

    let svcsHtml = '';
    if (data.nearby_services && data.nearby_services.length > 0) {
      svcsHtml = '<div class="em-services-list">';
      data.nearby_services.forEach(s => {
        let icon = 'fa-hospital';
        if (s.type === 'police') icon = 'fa-car-burst';
        if (s.type === 'ambulance') icon = 'fa-truck-medical';
        svcsHtml += `<div class="em-svc"><i class="fas ${icon}"></i> ${s.name} (${s.distance_km} km)</div>`;
      });
      svcsHtml += '</div>';
    }

    emModalBody.innerHTML = `
      <div class="em-modal-row">
        <i class="fas fa-location-crosshairs"></i>
        <span><strong>Location:</strong> ${data.location.address}</span>
      </div>
      <div class="em-modal-row">
        <i class="fas fa-truck-medical"></i>
        <span><strong>Nearest Responders:</strong></span>
      </div>
      ${svcsHtml}
      <div class="em-modal-row" style="margin-top:.5rem;">
        <i class="fas fa-microphone-lines"></i>
        <span><strong>Auto-Voice Message:</strong><br/>"${data.voice_message}"</span>
      </div>
    `;

    emModal.classList.remove('hidden');
    document.getElementById('em-info-card').classList.add('card-flash');
  }

  emModalClose.addEventListener('click', () => {
    emModal.classList.add('hidden');
    overlay.classList.add('hidden');
    setOverallStatus('warn');
    document.getElementById('em-info-card').classList.remove('card-flash');
  });

  elBtnClear.addEventListener('click', () => {
    elLog.innerHTML = '';
    alertCount = 0;
    elStatAlerts.textContent = alertCount;
    setOverallStatus('safe');
  });

  function switchToDashboardTab() {
    const navDashboard = document.querySelector('.nav-item[data-tab="dashboard"]');
    if (navDashboard && !navDashboard.classList.contains('active')) {
      navDashboard.click();
    }
  }

  // ════════════════════════════════════════════════════════════════
  // 4. Socket Events
  // ════════════════════════════════════════════════════════════════
  socket.on('new_alert', (data) => {
    console.log('New Alert:', data);
    appendLog(data.type, data.message, data.severity, data.timestamp);

    if (data.location) {
      updateMapLocation(data.location.lat, data.location.lng, data.message, data.severity === 'critical');
    }

    if (data.severity === 'critical') {
      triggerAlarmOverlay();
      switchToDashboardTab();
    } else if (data.severity === 'high') {
      setOverallStatus('warn');
    }
  });

  socket.on('emergency_escalation', (data) => {
    console.log('Emergency Escalation:', data);
    
    // Update sidebar info
    document.getElementById('em-number').textContent = data.emergency_number;
    document.getElementById('em-number').href = `tel:${data.emergency_number}`;
    document.getElementById('em-map-link').href = data.location.maps_link;

    const svcContainer = document.getElementById('em-services');
    if (data.nearby_services && data.nearby_services.length > 0) {
      svcContainer.innerHTML = '';
      data.nearby_services.forEach(s => {
        let cls = 'hospital', icn = 'fa-hospital';
        if (s.type === 'police') { cls = 'police'; icn = 'fa-car-burst'; }
        if (s.type === 'ambulance') { cls = 'ambulance'; icn = 'fa-truck-medical'; }
        svcContainer.innerHTML += `<div class="svc-item ${cls}"><i class="fas ${icn}"></i> <span>${s.name} — ${s.distance_km} km</span></div>`;
      });
    }

    if (data.incident_type !== 'system_init') {
      appendLog('EMERGENCY_ESCALATION', `Full emergency workflow activated. Notifying ${data.emergency_number}.`, 'critical', data.timestamp);
      triggerAlarmOverlay();
      switchToDashboardTab();
      showEmergencyModal(data);
    }
  });

  // SOS Button
  elBtnSos.addEventListener('click', () => {
    socket.emit('trigger_sos', { user: 'Admin' });
  });

  // ════════════════════════════════════════════════════════════════
  // 5. CCTV Upload & Status Polling
  // ════════════════════════════════════════════════════════════════
  elCctvInput.addEventListener('change', (e) => {
    if (e.target.files.length > 0) {
      elFileLabel.textContent = e.target.files[0].name;
    } else {
      elFileLabel.textContent = 'Choose accident video…';
    }
  });

  elBtnUpload.addEventListener('click', async () => {
    const file = elCctvInput.files[0];
    if (!file) return alert('Please select a video file first.');

    const fd = new FormData();
    fd.append('video', file);

    elBtnUpload.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Loading';
    elBtnUpload.disabled = true;

    try {
      const res = await fetch('/api/upload_cctv_video', { method: 'POST', body: fd });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Upload failed');
      appendLog('CCTV_SYSTEM', data.message, 'info');
      
      // Clear input so same file can be selected again if needed
      elCctvInput.value = '';
      elFileLabel.textContent = 'Choose accident video…';
    } catch (err) {
      appendLog('UPLOAD_ERROR', err.message, 'warning');
      alert(err.message);
    } finally {
      elBtnUpload.innerHTML = '<i class="fas fa-cloud-upload-alt"></i> Analyse';
      elBtnUpload.disabled = false;
    }
  });

  function updateStatusPill(elId, text) {
    const el = document.getElementById(elId);
    if (!el) return;
    
    let cls = 'status-pill ';
    let dot = 'green-dot';
    const lower = text.toLowerCase();
    
    if (lower.includes('crash') || lower.includes('emergency') || lower.includes('alert')) {
      cls += 'danger'; dot = 'red-dot';
    } else if (lower.includes('drowsy') || lower.includes('warning') || lower.includes('no driver')) {
      cls += 'warn'; dot = 'orange-dot';
    } else if (lower.includes('waiting') || lower.includes('no video') || lower.includes('error')) {
      dot = 'grey-dot';
    }

    el.className = cls;
    el.innerHTML = `<span class="dot ${dot}"></span> ${text}`;
  }

  // Update logic chips based on driver status string
  function updateChips(status) {
    const s = status.toLowerCase();
    const cEyes = document.getElementById('chip-eyes');
    const cChest = document.getElementById('chip-chest');
    const cPose = document.getElementById('chip-pose');
    
    if (s.includes('drowsy') || s.includes('eyes')) {
      cEyes.className = 'chip chip-danger';
      cEyes.innerHTML = '<i class="fas fa-eye-slash"></i> Eyes Closed';
    } else {
      cEyes.className = 'chip chip-ok';
      cEyes.innerHTML = '<i class="fas fa-eye"></i> Eyes Open';
    }

    if (s.includes('chest')) {
      cChest.className = 'chip chip-danger';
      cChest.innerHTML = '<i class="fas fa-heart-crack"></i> Chest Pain';
    } else {
      cChest.className = 'chip chip-ok';
      cChest.innerHTML = '<i class="fas fa-heart-pulse"></i> Chest OK';
    }

    if (s.includes('collapse')) {
      cPose.className = 'chip chip-danger';
      cPose.innerHTML = '<i class="fas fa-person-falling"></i> Collapse!';
    } else {
      cPose.className = 'chip chip-ok';
      cPose.innerHTML = '<i class="fas fa-person"></i> Posture OK';
    }
  }

  setInterval(async () => {
    try {
      const res = await fetch('/api/status');
      const data = await res.json();
      updateStatusPill('driver-status-pill', data.driver_status);
      updateStatusPill('cctv-status-pill', data.cctv_status);
      updateChips(data.driver_status);
    } catch (e) {
      // silently fail if server reboots
    }
  }, 1000);

  // ════════════════════════════════════════════════════════════════
  // 6. Tab Navigation Logic
  // ════════════════════════════════════════════════════════════════
  const navItems = document.querySelectorAll('.nav-item');
  const tabContents = document.querySelectorAll('.tab-content');

  navItems.forEach(item => {
    item.addEventListener('click', () => {
      // Remove active from all navs
      navItems.forEach(nav => nav.classList.remove('active'));
      item.classList.add('active');

      const targetTab = item.getAttribute('data-tab');

      // Hide all tabs
      tabContents.forEach(tab => tab.classList.add('hidden'));

      // Show target tab
      const tabToShow = document.getElementById('tab-' + targetTab);
      if (tabToShow) {
        tabToShow.classList.remove('hidden');
        // Fix Leaflet map sizing issue when switching back to dashboard
        if (targetTab === 'dashboard') {
          setTimeout(() => { map.invalidateSize(); }, 100);
        }
      }
    });
  });

  // ════════════════════════════════════════════════════════════════
  // 7. Stop Feed Logic
  // ════════════════════════════════════════════════════════════════
  const btnStopDriver = document.getElementById('stop-driver-feed');
  const driverFeedImg = document.getElementById('driver-feed-img');
  let driverFeedActive = true;
  
  if (btnStopDriver && driverFeedImg) {
    btnStopDriver.addEventListener('click', () => {
      if (driverFeedActive) {
        driverFeedImg.src = 'https://placehold.co/640x480/0f172a/3b82f6?text=Camera+Stopped';
        btnStopDriver.innerHTML = '<i class="fas fa-play"></i>';
        btnStopDriver.title = "Start Driver Camera";
        driverFeedActive = false;
        updateStatusPill('driver-status-pill', "Camera Stopped");
      } else {
        driverFeedImg.src = '/driver_video_feed?' + new Date().getTime();
        btnStopDriver.innerHTML = '<i class="fas fa-times"></i>';
        btnStopDriver.title = "Stop Driver Camera";
        driverFeedActive = true;
      }
    });
  }

  const btnStopCctv = document.getElementById('stop-cctv-feed');
  const cctvFeedImg = document.getElementById('cctv-feed-img');
  let cctvFeedActive = true;
  
  if (btnStopCctv && cctvFeedImg) {
    btnStopCctv.addEventListener('click', () => {
      if (cctvFeedActive) {
        cctvFeedImg.src = 'https://placehold.co/640x480/0f172a/8b5cf6?text=CCTV+Stopped';
        btnStopCctv.innerHTML = '<i class="fas fa-play"></i>';
        btnStopCctv.title = "Start CCTV Feed";
        cctvFeedActive = false;
        updateStatusPill('cctv-status-pill', "Video Stopped");
      } else {
        cctvFeedImg.src = '/cctv_video_feed?' + new Date().getTime();
        btnStopCctv.innerHTML = '<i class="fas fa-times"></i>';
        btnStopCctv.title = "Stop CCTV Feed";
        cctvFeedActive = true;
      }
    });
  }

});
