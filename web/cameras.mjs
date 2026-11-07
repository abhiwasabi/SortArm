const previewMode = new URLSearchParams(location.search).get("demo") === "1";
const ui = {usbCameraDevice: document.getElementById("usbCameraDevice")};

if (!previewMode) fetch('/api/cameras').then(response => response.json()).then(data => {
  ui.usbCameraDevice.replaceChildren();
  for (const device of data.devices || []) {
    const option = document.createElement('option');
    option.value = device.id;
    option.textContent = device.name;
    ui.usbCameraDevice.append(option);
  }
  const builtin = (data.devices || []).find(d => /macbook|facetime|built.in/i.test(d.name));
  if (builtin) ui.usbCameraDevice.value = builtin.id;
  if (!data.devices?.length) {
    const option = document.createElement('option');
    option.value = '';
    option.textContent = 'No camera detected';
    ui.usbCameraDevice.append(option);
  }
}).catch(() => {});
