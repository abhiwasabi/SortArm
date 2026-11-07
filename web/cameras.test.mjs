import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import {chooseCameraDevice} from './camera-choice.mjs';
function harness() {
  const nodes = {};
  const pending = [];
  const document = {getElementById(id) {return nodes[id] ??= {value:'',dataset:{},listeners:{},addEventListener(event,fn){(this.listeners[event]??=[]).push(fn);},dispatchEvent(event){for(const fn of this.listeners[event.type]||[])fn(event);}};}};
  document.getElementById('cameraSourceSelect').value='usb';
  document.getElementById('usbCameraDevice').value='old-device';
  const context = vm.createContext({document, location:{search:''}, URLSearchParams,AbortController,Event,chooseCameraDevice,
    setTimeout:()=>1, clearTimeout(){}, fetch:(url)=>new Promise(resolve=>pending.push({url,resolve}))});
  const code=readFileSync(new URL('./cameras.mjs',import.meta.url),'utf8').replace(/^import .*\r?\n/,'');
  vm.runInContext(code,context);
  return {nodes,pending};
}
const settle=()=>new Promise(resolve=>setImmediate(resolve));
const response=data=>({ok:true,json:async()=>data});
test('discovery immediately clears the previous device ID',()=>{
 const h=harness();
 assert.equal(h.nodes.usbCameraDevice.value,'');
 h.nodes.usbCameraDevice.value='laptop';
 h.nodes.cameraSourceSelect.value='iphone';
 h.nodes.cameraSourceSelect.dispatchEvent(new Event('change'));
 assert.equal(h.nodes.usbCameraDevice.value,'');
});
test('stale initial discovery cannot override an explicit ESP32 choice',async()=>{
 const h=harness();
 h.nodes.cameraSourceSelect.value='esp32';
 h.nodes.cameraSourceSelect.dispatchEvent(new Event('change'));
 h.pending[0].resolve(response({devices:[{id:'phone',name:'iPhone Camera'}]}));
 await settle();
 assert.equal(h.nodes.cameraSourceSelect.value,'esp32');
 assert.equal(h.nodes.usbCameraDevice.value,'');
 assert.equal(h.pending.length,1);
});
