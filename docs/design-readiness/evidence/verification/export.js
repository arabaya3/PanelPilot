// Export a designed project (from a saved design response) in several formats. Stateless.
const fs=require('fs');const [tokFile,resp,prefix,fmts]=process.argv.slice(2);
const TOKEN=fs.readFileSync(tokFile,'utf8').trim();const d=JSON.parse(fs.readFileSync(resp));
(async()=>{for(const format of fmts.split(',')){
 const r=await fetch('http://127.0.0.1:8000/api/v1/design/export',{method:'POST',headers:{'content-type':'application/json',authorization:'Bearer '+TOKEN},body:JSON.stringify({project:d.project,format})});
 const buf=Buffer.from(await r.arrayBuffer());const ext=format.endsWith('_csv')?'csv':format.endsWith('pdf')?'pdf':format==='json'?'json':format;
 const out=`exports/${prefix}.${format}.${ext}`;fs.writeFileSync(out,buf);console.log(prefix,format,r.status,buf.length,r.status!==200?buf.toString().slice(0,300):'');}})();
