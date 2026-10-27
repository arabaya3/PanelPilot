const fs=require('fs');for(const f of process.argv.slice(2)){const d=JSON.parse(fs.readFileSync(f));console.log('=== '+f);
for(const b of d.project.boards){console.log('board',b.name,'supply',JSON.stringify(b.supply),'incomers',b.incomer_ids);
for(const c of b.circuits)console.log(' C',JSON.stringify(c));
for(const x of b.devices)console.log(' D',x.id,x.kind,x.rating_a??'',JSON.stringify(x).slice(0,260));
for(const x of b.cables)console.log(' K',JSON.stringify(x));
for(const n of b.notes)console.log(' N',n.code,JSON.stringify(n.params));}}
