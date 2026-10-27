// Per-family API trials. All numeric inputs are TEST VALUES, not from any reference project.
const fs=require('fs');const TOKEN=fs.readFileSync(process.argv[2],'utf8').trim();
const API='http://127.0.0.1:8000/api/v1/design';
const info={name:'TEST - verification',number:'TEST-001',customer:'TEST',revisions:[{index:'A',date:'2026-10-23',description:'TEST issue',drawn_by:'V4'}]};
const supply={voltage_v:400,phases:3,frequency_hz:50,earthing:'TN-S'}; // TEST VALUE; fault level & Ze unknown -> omitted
const L=(description,load,power_kw,phases,extra={})=>({description,load,power_kw,phases,...extra});
const trials={
 hall_plain:{path:'/distribution-board',body:{info,board:{name:'DB-HALL',supply,loads:[
  L('Hall lighting row 1 (TEST VALUE)','lighting',1.2,1,{length_m:30}),
  L('Hall lighting row 2 (TEST VALUE)','lighting',1.2,1,{length_m:35}),
  L('Sockets hall east (TEST VALUE)','socket',2,1,{length_m:25}),
  L('AC unit (TEST VALUE)','air_conditioning',5,3,{length_m:20})]}}},
 hall_knx_kind:{path:'/distribution-board',body:{info,board:{name:'DB-HALL',supply,loads:[L('KNX dimmed lighting (TEST VALUE)','knx',1.2,1)]}}},
 pump_soft_starter:{path:'/distribution-board',body:{info,board:{name:'PCP-1',supply,loads:[L('Pump (TEST VALUE)','motor',15,3,{starter:'soft_starter'})]}}},
 pool:{path:'/distribution-board',body:{info,board:{name:'DB-POOL',supply,loads:[
  L('Pool filtration pump (TEST VALUE)','motor',2.2,3,{starter:'dol',length_m:15}),
  L('Pool underwater lighting (TEST VALUE)','lighting',0.4,1,{length_m:20}),
  L('Pool heater (TEST VALUE)','water_heater',6,3,{length_m:15}),
  L('Plant room sockets (TEST VALUE)','socket',1.5,1,{length_m:10})]}}},
 mdb_ats_extra_field:{path:'/distribution-board',body:{info,board:{name:'MDB',supply:{...supply,source:'generator',standby_supply:{voltage_v:400}},transfer_switch:{type:'ATS'},loads:[
  L('Feeder to DB-1 (TEST VALUE)','sub_board',40,3)]}}},
 fire_pump_kind:{path:'/distribution-board',body:{info,board:{name:'FPC',supply,loads:[L('Fire pump (TEST VALUE)','fire_pump',30,3)]}}},
 fire_pump_as_motor:{path:'/distribution-board',body:{info,board:{name:'FPC',supply,loads:[L('Fire pump - main (TEST VALUE)','motor',30,3,{starter:'star_delta',length_m:20}),L('Jockey pump (TEST VALUE)','motor',2.2,3,{starter:'dol',length_m:20})]}}},
};
(async()=>{for(const [k,t] of Object.entries(trials)){
 if(process.argv[3]&&!process.argv[3].split(',').includes(k))continue;
 fs.writeFileSync(`requests/${k}.json`,JSON.stringify(t.body,null,1));
 const r=await fetch(API+t.path,{method:'POST',headers:{'content-type':'application/json',authorization:'Bearer '+TOKEN},body:JSON.stringify(t.body)});
 const txt=await r.text();fs.writeFileSync(`responses/${k}.json`,txt);
 console.log(k,r.status,txt.length,r.status!==200?txt.slice(0,400):'');}})();
