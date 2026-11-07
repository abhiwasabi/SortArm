export function chooseCameraDevice(devices, kind) {
  return devices.find(device => kind === 'iphone'
    ? /iphone|continuity/i.test(device.name)
    : /macbook|facetime|built.in|integrated/i.test(device.name) && !/iphone|continuity/i.test(device.name)) || null;
}
