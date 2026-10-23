const fs=require('fs');const API='http://127.0.0.1:8000/api/v1';
const REQ=require(require('path').join(__dirname, '..', '..', 'evidence', 'backend', 'design-project-request.json'));
const F=['pdf','calculations_pdf','dxf','qet','aml','devices_csv','parts_csv','cables_csv','circuits_csv','terminals_csv','eplan_devices_csv','ace_components_csv','ace_terminals_csv','quotation_pdf','quotation_csv','plc_st','plc_io_csv','json'];
const c=async(m,u,t,b)=>{const r=await fetch(API+u,{method:m,headers:{'Content-Type':'application/json',...(t?{Authorization:'Bearer '+t}:{})},body:b&&JSON.stringify(b)});return {s:r.status,buf:Buffer.from(await r.arrayBuffer())}};
(async()=>{const su=await c('POST','/auth/signup',null,{email:`e2e-p1a-u-${Date.now()}@example.com`,password:require('crypto').randomBytes(18).toString('base64url')});const t=JSON.parse(su.buf).access_token;
const sv=JSON.parse((await c('POST','/design/projects',t,{name:'ADV unapproved',request:REQ})).buf);const res={hash:sv.snapshot_sha256};
for(const f of F){const b={format:f};if(f.startsWith('quotation'))b.pricing={currency:'JOD'};const r=await c('POST',`/design/projects/${sv.id}/revisions/1/export`,t,b);res[f]=r.s;if(r.s===200)fs.writeFileSync(process.argv[2]+'/unapproved.'+f,r.buf);}
console.log(JSON.stringify(res));})();
