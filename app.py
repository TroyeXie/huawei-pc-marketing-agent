"""Zhi Hua: a source-grounded Huawei PC marketing agent.

The public server exposes generated work only. Credentials, source documents and
raw knowledge-base indexes remain outside the source tree and public routes.
"""
from __future__ import annotations
import os, json, time, uuid, threading, hashlib, mimetypes, logging, shutil, re, html, io, zipfile, base64, copy
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict, deque
from typing import Any
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE = Path(__file__).resolve().parent
RUNTIME = Path(os.getenv('ZHIHUA_RUNTIME_DIR', '/tmp/zhi-hua-runtime')).resolve()
RUNTIME.mkdir(parents=True, exist_ok=True)
GITHUB_URL = os.getenv('GITHUB_URL', 'https://github.com/TroyeXie/huawei-pc-marketing-agent')
MODEL_URL = os.getenv('MODEL_API_URL', '')
PRODUCTS = [
 {'id':'matebook14-ultra','name':'MateBook 14 酷睿 Ultra'},
 {'id':'matebook14-harmony','name':'HUAWEI MateBook 14（鸿蒙版）'},
 {'id':'matebook-pro','name':'HUAWEI MateBook Pro'},
 {'id':'matebook-pro-s','name':'HUAWEI MateBook Pro S'},
 {'id':'matebook-fold','name':'HUAWEI MateBook Fold 非凡大师'},
 {'id':'matebook-d14','name':'HUAWEI MateBook D 14 2024'},
 {'id':'matebook-d16','name':'HUAWEI MateBook D 16 2024'},
 {'id':'matebook-d16-se','name':'HUAWEI MateBook D 16 SE 版 2024'},
 {'id':'matebook-gt','name':'HUAWEI MateBook GT 14'},
 {'id':'matebook-x','name':'HUAWEI MateBook X Pro 酷睿 Ultra 微绒典藏版'},
]
SCENES=['开学季','职场办公','内容创作','家庭换新','企业采购','门店到访','新品首发','节日营销','生态体验']
PLATFORMS=['小红书','公众号','门店朋友圈','门店海报']
app=FastAPI(title='知华·有据',docs_url=None,redoc_url=None,openapi_url=None)
app.mount('/static',StaticFiles(directory=BASE/'static'),name='static')
_lock=threading.RLock()
_run_lock=threading.BoundedSemaphore(int(os.getenv('MAX_CONCURRENT_RUNS','2')))
_client=None
_runs:dict[str,dict]={}
_media:dict[tuple[str,str],Path]={}
_limits=defaultdict(deque)
_status_cache={'at':0.,'data':{}}
_examples_cache=None
SNAPSHOT_PATH=BASE/'data'/'ima_verified_cache.json'

def cache_index():
 if not SNAPSHOT_PATH.is_file():return {}
 data=json.loads(SNAPSHOT_PATH.read_text(encoding='utf-8'))
 return data.get('products',{}) if data.get('schema_version')==1 else {}

def use_verified_cache():
 return os.getenv('IMA_DATA_MODE','verified_cache')!='live' and bool(cache_index())

def cached_materials(product):
 materials=cache_index().get(product)
 if not materials:raise ValueError('该型号尚无已核验的 ima 检索缓存。')
 asset_root=(BASE/'data'/'ima_assets').resolve()
 selected_dir=RUNTIME/'ima-selected'
 selected_dir.mkdir(parents=True,exist_ok=True)
 for img in materials.get('images',[]):
  filename=img.pop('asset_filename','')
  if not re.fullmatch(r'[a-f0-9]{64}\.(?:png|jpe?g|webp)',filename):continue
  source=(asset_root/filename).resolve()
  if not source.is_relative_to(asset_root) or not source.is_file():continue
  destination=selected_dir/filename
  if not destination.is_file():shutil.copyfile(source,destination)
  img['local_path']=str(destination)
 materials['cache']['used_at']=current_time()
 return materials

def acquire_materials(product):
 if use_verified_cache():return cached_materials(product),'verified_cache'
 try:return client().collect_product(product),'live'
 except Exception:
  if product not in cache_index():raise
  return cached_materials(product),'verified_cache_fallback'

class GenerateRequest(BaseModel):
 product:str=Field(default='auto',max_length=120)
 audience:str=Field(default='',max_length=200)
 scene:str=Field(default='',max_length=100)
 platform:str=Field(default='',max_length=80)
 goal:str=Field(default='',max_length=500)
 brief:str=Field(default='',max_length=3000)
 customer_context:dict=Field(default_factory=dict)
 quote_inputs:dict=Field(default_factory=dict)
 store_profile:dict=Field(default_factory=dict)
 content_angle:str=Field(default='',max_length=300)
 content_nonce:str=Field(default='',max_length=100)
class CheckRequest(BaseModel):
 text:str=Field(max_length=15000)
 product:str=Field(default='',max_length=120)
 run_id:str=Field(default='',max_length=64)
class ReviseRequest(BaseModel):
 run_id:str=Field(min_length=8,max_length=64)
 title:str=Field(min_length=1,max_length=300)
 text:str=Field(min_length=1,max_length=15000)

@app.post('/api/intake')
def intake(body:GenerateRequest):
 from personalization import quick_intake
 return quick_intake(body.model_dump(),PRODUCTS,SCENES,PLATFORMS)

@app.middleware('http')
async def headers(request:Request,call_next):
 try: content_length=int(request.headers.get('content-length','0') or 0)
 except ValueError: return JSONResponse({'status':'error','message':'请求长度格式不正确。'},status_code=400)
 if content_length>100_000:
  return JSONResponse({'status':'error','message':'内容过长，请缩短任务描述。'},status_code=413)
 response=await call_next(request)
 response.headers['X-Content-Type-Options']='nosniff'
 response.headers['Referrer-Policy']='same-origin'
 if request.url.path.startswith('/api/'):
  response.headers['Cache-Control']='no-store'
 if request.url.path in ('/static/app.js','/static/styles.css'):
  response.headers['Cache-Control']='no-cache, max-age=0, must-revalidate'
 return response

def client():
 global _client
 with _lock:
  if _client is None:
   from ima_client import IMAClient
   _client=IMAClient()
  return _client

def current_time():
 return datetime.now(timezone.utc).isoformat(timespec='seconds')

def normalize_edit_sources(result):
 """Keep review material separate from the facts actually cited in edited copy."""
 if result.get('mode')!='verified_edit':return result
 from engine import edited_citation_ids,_render_markdown
 citations=edited_citation_ids(result)
 sections=result.get('article',{}).get('sections',[])
 for section in sections:section['fact_ids']=citations['body'] if len(sections)==1 else []
 result.setdefault('edit_verification',{}).update(citation_binding_version=1,
   citation_mapping_available=citations['mapped'],title_fact_ids=citations['title'],body_fact_ids=citations['body'])
 result['markdown']=_render_markdown(result)
 return result

def restore_run(run_id):
 """Recover an existing run and only its own selected images after restart."""
 if not re.fullmatch(r'[A-Za-z0-9_-]{8,64}',str(run_id)):return None
 with _lock:
  if run_id in _runs:return normalize_edit_sources(_runs[run_id])
 path=RUNTIME/'runs'/f'{run_id}.json'
 try:
  if not path.is_file() or path.stat().st_size>3_000_000:return None
  record=json.loads(path.read_text(encoding='utf-8'))
  result=record.get('result',{}) if record.get('schema_version')==2 else record
  if not isinstance(result,dict) or result.get('run_id')!=run_id:return None
  media=record.get('media',{}) if record.get('schema_version')==2 else {}
  if not isinstance(media,dict):media={}
  for image_id,filename in media.items():
   if not re.fullmatch(r'[a-f0-9]{18}',str(image_id)):continue
   if not re.fullmatch(r'[a-f0-9]{64}\.(?:png|jpe?g|webp)',str(filename)):continue
   selected=RUNTIME/'ima-selected'/filename
   if selected.is_file() and not selected.is_symlink():_media[(run_id,image_id)]=selected
  result=normalize_edit_sources(scrub(result))
  with _lock:_runs[run_id]=result
  return result
 except (OSError,ValueError,TypeError):return None

def save_run(result):
 run_id=result['run_id']
 manifest={image_id:path.name for (registered_run,image_id),path in list(_media.items()) if registered_run==run_id}
 folder=RUNTIME/'runs'
 folder.mkdir(exist_ok=True)
 target=folder/f'{run_id}.json'
 temporary=folder/f'{run_id}.tmp'
 temporary.write_text(json.dumps({'schema_version':2,'result':result,'media':manifest},ensure_ascii=False,indent=2),encoding='utf-8')
 temporary.replace(target)

def recent_runs(product,platform):
 folder=RUNTIME/'runs'
 if folder.is_dir():
  try:
   paths=sorted(folder.glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:30]
   for path in paths:restore_run(path.stem)
  except OSError:pass
 return [run for run in list(_runs.values()) if run.get('status') in ('success','ok')
         and run.get('input',{}).get('product')==product and run.get('input',{}).get('platform')==platform][-30:]

def scrub(value:Any):
 """Do not return source caches, credentials or private file addresses."""
 if isinstance(value,dict):
  return {k:scrub(v) for k,v in value.items() if k not in {'local_path','kb_id','knowledge_base_id','media_id','headers','api_key','client_id','raw','raw_response','signed_url'}}
 if isinstance(value,list): return [scrub(x) for x in value]
 if isinstance(value,str):
  for key in ['IMA_OPENAPI_CLIENTID','IMA_OPENAPI_APIKEY','MODEL_API_KEY']:
   secret=os.getenv(key,'')
   if secret: value=value.replace(secret,'[已隐藏]')
  if value.startswith(str(RUNTIME)): return '[内部素材]'
  return value
 return value

def resolve_product(v,brief=''):
 for p in PRODUCTS:
  if v in (p['id'],p['name']):return p['name']
 if v in ('','auto'):
  matches=[p['name'] for p in PRODUCTS if p['name'].replace('HUAWEI ','') in brief]
  if len(matches)==1:return matches[0]
 return v

@app.get('/')
def home():return FileResponse(BASE/'static/index.html',headers={'Cache-Control':'no-cache'})
@app.get('/healthz')
def health():return {'status':'ok','application':'zhi-hua-agent'}
@app.get('/api/catalog')
def catalog():
 from public_evidence import supported_products
 available=set(supported_products())
 snapshots=cache_index()
 from personalization import STORE_TYPES,VOICES
 return {'products':[dict(p,available=p['name'] in available,availability_note='' if p['name'] in available else '来源待核对',image_count=snapshots.get(p['name'],{}).get('cache',{}).get('image_count'),generation_tested=snapshots.get(p['name'],{}).get('cache',{}).get('generation_tested',False)) for p in PRODUCTS],'platforms':PLATFORMS,'scenes':SCENES,'store_types':STORE_TYPES,'voices':VOICES}

@app.get('/api/ecosystem')
def ecosystem_catalog():
 from ecosystem import catalog as ecosystem_sources
 data=ecosystem_sources()
 return {key:data.get(key,[] if key in ('sources','recommended_examples') else '') for key in ('provider','verified_at','scope','sources','recommended_examples')}

def source_freshness():
 from public_evidence import _catalog
 references={item['product_name']:item.get('verified_at','') for item in _catalog().get('products',[])}
 products=[]
 now=datetime.now(timezone.utc)
 for name,materials in cache_index().items():
  timestamp=materials.get('cache',{}).get('source_retrieved_at','')
  if isinstance(timestamp,list):timestamp=max([str(item) for item in timestamp],default='')
  age=None
  try:
   parsed=datetime.fromisoformat(str(timestamp).replace('Z','+00:00'))
   if parsed.tzinfo:age=round(max((now-parsed).total_seconds()/3600,0),1)
  except (ValueError,TypeError):pass
  products.append({'product':name,'source_retrieved_at':timestamp,'age_hours':age,'status':'cached' if age is not None else 'unknown','official_verified_at':references.get(name,'')})
 return {'checked_at':current_time(),'products':products,'live_requested':os.getenv('IMA_DATA_MODE','verified_cache')=='live','live_connected':False}
@app.get('/api/status')
def status():
 now=time.monotonic()
 if _status_cache['data'] and now-_status_cache['at']<30:return _status_cache['data']
 output={'ima_connected':False,'ima_configured':bool(os.getenv('IMA_OPENAPI_CLIENTID') and os.getenv('IMA_OPENAPI_APIKEY')),'knowledge_base':'任德耀新建华为PC知识库','account_label':'参赛团队 ima 账号','llm_configured':bool(os.getenv('MODEL_API_KEY') and os.getenv('MODEL_API_URL') and os.getenv('MODEL_NAME')),'mode':'live','github_url':GITHUB_URL,'message':'','source_freshness':source_freshness()}
 if use_verified_cache():
  snapshots=cache_index()
  bases=sorted({str(value.get('kb_name','')) for value in snapshots.values() if value.get('kb_name')})
  output.update(source_available=True,source_mode='verified_cache',mode='verified_ima_cache',knowledge_base=' / '.join(bases),cached_products=list(snapshots),cache_source_times={name:value.get('cache',{}).get('source_retrieved_at',[]) for name,value in snapshots.items()},message='产品事实为实际 ima 检索并经官网规格核对的资料；配图按来源分别取自知识库上传档案或华为官网，均与官方文件核对。内容由模型现场生成。')
  _status_cache.update(at=now,data=output)
  return output
 try:
  data=client().discover()
  output.update({'ima_connected':data.get('status') in ('connected','ok','success',True),'knowledge_base':data.get('kb_name',output['knowledge_base']),'account_label':data.get('account_label',output['account_label']),'message':data.get('message','共享知识库连接成功。')})
  output['source_freshness']['live_connected']=output['ima_connected']
 except Exception as e:
  output['message']='ima 连接暂不可用：'+str(e)
 if not output['llm_configured']:output['message']+=' 模型服务尚未配置。'
 output=scrub(output)
 _status_cache.update(at=now,data=output)
 return output

def preflight(payload):
 questions=[]
 if payload['product'] in ('','auto'):questions.append('请指定产品完整型号；例如 MateBook 14 酷睿 Ultra，或 MateBook 14 鸿蒙版。')
 elif payload['product'].replace('HUAWEI','').strip().lower() in ('matebook 14','matebook14'):
  questions.append('MateBook 14 有不同平台版本。请选择酷睿 Ultra 版或鸿蒙版，以免参数和图片混用。')
 elif payload['product'] not in {p['name'] for p in PRODUCTS}:
  questions.append('请从产品列表选择一个完整型号；比较多个型号时，请先分别核对后再组织对比。')
 else:
  from public_evidence import supported_products
  if payload['product'] not in supported_products():questions.append('该型号的来源核对尚未完成。请先选择已核对型号，再指定人群、场景和平台。')
 if not payload['audience'].strip():questions.append('内容面向哪类人群？例如大学新生、差旅办公者或附近到店顾客。')
 if not payload['scene'].strip():questions.append('本次使用场景是什么？例如开学季、职场办公或门店到访。')
 if payload['platform'] not in PLATFORMS:questions.append('请选择小红书、公众号、门店朋友圈或门店海报。')
 return questions

def normalize_materials(materials,run_id):
 """Register only selected product assets behind opaque per-run paths."""
 out=dict(materials)
 selected=[]
 for i,img in enumerate(materials.get('images',[])[:12]):
  im=dict(img)
  path=im.get('local_path')
  if path and Path(path).is_file():
   full=Path(path).resolve()
   if full.is_relative_to(RUNTIME):
    ident=hashlib.sha256((run_id+str(i)+str(full)).encode()).hexdigest()[:18]
    _media[(run_id,ident)]=full
    im.update(id=ident,url=f'/api/media/{run_id}/{ident}',status=im.get('status','verified'),model=materials.get('product_name',''))
  source=im.get('source',{})
  if isinstance(source,dict):
   im.setdefault('source_title',source.get('title',im.get('title','ima 图片')))
   im['source_url']=im.get('public_source_url') or source.get('public_source_url') or 'https://ima.qq.com/wikis'
  im.setdefault('source_url','https://ima.qq.com/wikis')
  selected.append(im)
 out['images']=selected
 for doc in out.get('documents',[]):
  if isinstance(doc.get('source'),dict):
   doc['source']['url']=doc['source'].get('public_source_url') or 'https://ima.qq.com/wikis'
 return out

@app.post('/api/generate')
def generate(body:GenerateRequest,request:Request):
 payload=body.model_dump()
 from personalization import normalize_store_profile,novelty_report
 payload['store_profile']=normalize_store_profile(payload.get('store_profile'))
 payload['product']=resolve_product(payload['product'],payload['brief'])
 questions=preflight(payload)
 if questions:return {'status':'needs_clarification','questions':questions,'message':'补充这些信息后即可生成。','mode':'live'}
 host=request.client.host if request.client else 'unknown'
 now=time.monotonic()
 with _lock:
  q=_limits[host]
  while q and now-q[0]>3600:q.popleft()
  if len(q)>=int(os.getenv('MAX_RUNS_PER_HOUR','24')):
   return JSONResponse({'status':'error','message':'本小时生成次数较多，请稍后再试。'},status_code=429)
  q.append(now)
 if not _run_lock.acquire(blocking=False):return JSONResponse({'status':'error','message':'当前任务正在运行，请稍后再试。'},status_code=429)
 run_id=uuid.uuid4().hex
 started=time.monotonic()
 trace=[]
 try:
  from engine import generate as run_engine
  materials,source_mode=acquire_materials(payload['product'])
  cached=source_mode.startswith('verified_cache')
  source_times=materials.get('cache',{}).get('source_retrieved_at',[])
  if not source_times:
   source_times=sorted({str(item.get('source',{}).get('retrieved_at','')) for item in [*materials.get('documents',[]),*materials.get('images',[])] if isinstance(item,dict) and isinstance(item.get('source'),dict) and item['source'].get('retrieved_at')})
  trace.append({'step':'载入已核验 ima 检索缓存' if cached else 'ima 资料与图片检索','status':'completed','detail':f"指定共享库；产品：{payload['product']}；载入 {len(materials.get('documents',[]))} 份资料、{len(materials.get('images',[]))} 个图片候选。"+(f' 使用真实检索缓存，原始取材时间：{source_times}。' if cached else ''),'ms':int((time.monotonic()-started)*1000)})
  materials=normalize_materials(materials,run_id)
  result=run_engine(payload,materials)
  result['trace']=trace+result.get('trace',[])
  result.update(run_id=run_id,generated_at=current_time(),elapsed_ms=int((time.monotonic()-started)*1000),input=payload)
  result['material_provenance']={'provider':'ima OpenAPI','knowledge_base':materials.get('kb_name','任德耀新建华为PC知识库'),'source_mode':source_mode,'source_retrieved_at':source_times,'image_providers':sorted({str(i.get('source',{}).get('provider','')) for i in materials.get('images',[])}),'fresh_ima_retrieval_for_this_run':not cached,'note':('本次使用已实际检索并核验的 ima 事实资料；配图来源独立记录。本轮重新生成并核验。') if cached else '本次任务检索产品事实与图片，再执行生成和核验。'}
  result['material_provenance']['fact_providers']=sorted({str(f.get('provider') or 'ima') for f in result.get('facts',[])})
  if result.get('status') in ('success','ok'):
   result['novelty']=novelty_report(result.get('article',{}),recent_runs(payload['product'],payload['platform']))
  result=scrub(result)
  with _lock:_runs[run_id]=result
  save_run(result)
  return result
 except Exception as e:
  logging.getLogger('zhi-hua').warning('Generation failed: %s',type(e).__name__)
  return {'status':'error','message':scrub(str(e)),'trace':trace,'run_id':run_id,'mode':'live','elapsed_ms':int((time.monotonic()-started)*1000)}
 finally:_run_lock.release()

@app.get('/api/media/{run_id}/{image_id}')
def image_file(run_id:str,image_id:str):
 if (run_id,image_id) not in _media:restore_run(run_id)
 path=_media.get((run_id,image_id))
 if path is None or not path.is_file():raise HTTPException(404,'素材已过期，请重新生成。')
 media_type=mimetypes.guess_type(path.name)[0] or 'image/jpeg'
 if not media_type.startswith('image/'):raise HTTPException(404)
 return FileResponse(path,media_type=media_type,headers={'Cache-Control':'private, max-age=1800'})

@app.get('/api/examples')
def examples():
 global _examples_cache
 if _examples_cache is not None:return _examples_cache
 path=BASE/'examples'/'index.json'
 if not path.exists():return {'examples':[]}
 data=json.loads(path.read_text(encoding='utf-8'))
 for entry in data.get('examples',[]):
  result=normalize_edit_sources(entry.get('publication_result') or entry.get('result',{}))
  run_id=result.get('run_id')
  if run_id:
   with _lock:_runs.setdefault(run_id,result)
 _examples_cache=scrub(data)
 return _examples_cache

@app.post('/api/check')
def check(body:CheckRequest):
 from engine import verify_edited_text
 if body.run_id and not restore_run(body.run_id):examples()
 run=_runs.get(body.run_id,{}) if body.run_id else {}
 facts=run.get('facts',[]) if run.get('status') in ('success','ok') else []
 bound_product=run.get('input',{}).get('product') or run.get('strategy',{}).get('product','')
 requested=resolve_product(body.product) if body.product else bound_product
 if requested and bound_product and requested!=bound_product:
  return {'status':'unverified','checks':[],'fact_check_complete':False,'message':'当前型号与这份文稿的来源型号不同，请先用相应型号的资料重新生成。'}
 return scrub(verify_edited_text(body.text,facts,requested,run.get('input',{}).get('store_profile',{})))

@app.get('/api/runs/{run_id}')
def get_run(run_id:str):
 if not restore_run(run_id):examples()
 result=_runs.get(run_id)
 if result is None:raise HTTPException(404,'运行记录不存在。')
 return result

@app.post('/api/revise')
def revise(body:ReviseRequest):
 from engine import verify_edited_text,_render_markdown
 from personalization import novelty_report
 started=time.monotonic()
 if not restore_run(body.run_id):examples()
 original=_runs.get(body.run_id)
 if not original or original.get('status') not in ('success','ok'):
  raise HTTPException(404,'请先打开一份已完成的文稿，再进行修改。')
 title,text=body.title.strip(),body.text.strip()
 if not title or not text:return {'status':'unverified','checks':[],'message':'标题和正文都需要填写。'}
 product=original.get('input',{}).get('product','')
 verification=verify_edited_text(title+'\n'+text,original.get('facts',[]),product,original.get('input',{}).get('store_profile',{}))
 if verification['status']!='ok':return scrub(verification)
 updated=copy.deepcopy(original)
 updated.setdefault('original_generated_at',original.get('generated_at'))
 updated.setdefault('original_elapsed_ms',original.get('elapsed_ms'))
 run_id=uuid.uuid4().hex
 stamp=current_time()
 updated.update(run_id=run_id,parent_run_id=body.run_id,generated_at=stamp,edited_at=stamp,
                status='success',mode='verified_edit',message='修改稿已对照原始产品资料核对并另存。')
 # Body is the exact text the user reviewed. Original CTA and tags are not
 # silently appended or exported again after the edit.
 updated['article']={'title':title,'lead':'','sections':[{'heading':'','text':text,'fact_ids':[]}],'cta':'','tags':[]}
 updated['checks']=verification['checks']
 updated['trace']=[{'step':'用户修改','status':'completed','detail':'对照原运行的事实逐项重新核对，保留原稿并另存此版本。','ms':0}]
 updated['edit_verification']={'fact_check_complete':True,'parent_run_id':body.run_id,'checked_at':stamp}
 for index,img in enumerate(updated.get('images',[])):
  old_url=str(img.get('url',''))
  match=re.fullmatch(r'/api/media/([A-Za-z0-9_-]+)/([a-f0-9]{18})',old_url)
  if match:
   old_run,old_id=match.groups()
   if (old_run,old_id) not in _media:restore_run(old_run)
   path=_media.get((old_run,old_id))
   if path:
    new_id=hashlib.sha256((run_id+str(index)+path.name).encode()).hexdigest()[:18]
    _media[(run_id,new_id)]=path
    img['url']=f'/api/media/{run_id}/{new_id}'
  img['position']='正文之后'
 updated['novelty']=novelty_report(updated['article'],recent_runs(product,original.get('input',{}).get('platform','')))
 normalize_edit_sources(updated)
 updated['elapsed_ms']=int((time.monotonic()-started)*1000)
 updated=scrub(updated)
 with _lock:_runs[run_id]=updated
 save_run(updated)
 return updated

def _export_image_bytes(image):
 value=str(image.get('url',''))
 static_match=re.fullmatch(r'/static/example-assets/([a-f0-9]{64}\.(?:png|jpe?g|webp))',value)
 if static_match:
  path=BASE/'static'/'example-assets'/static_match.group(1)
  if path.is_file() and not path.is_symlink():return (path.read_bytes(),mimetypes.guess_type(path.name)[0] or 'image/jpeg',path.suffix)
  return None
 match=re.fullmatch(r'/api/media/([A-Za-z0-9_-]+)/([a-f0-9]{18})',value)
 if not match:return None
 run_id,image_id=match.groups()
 if (run_id,image_id) not in _media:restore_run(run_id)
 path=_media.get((run_id,image_id))
 if not path or not path.is_file():return None
 return (path.read_bytes(),mimetypes.guess_type(path.name)[0] or 'image/jpeg',path.suffix)

def render_html_export(data,embedded=True):
 """Portable article with exact edited text, local images and cited facts."""
 esc=lambda value:html.escape(str(value or ''),quote=True)
 def paragraphs(value):
  return ''.join('<p>'+esc(part)+'</p>' for part in str(value or '').split('\n') if part.strip())
 article=data.get('article',{})
 blocks=['<header><p class="brand">知华 · 有据</p><h1>'+esc(article.get('title'))+'</h1></header>']
 blocks.append('<article>'+paragraphs(article.get('lead')))
 for section in article.get('sections',[]):
  if section.get('heading'):blocks.append('<h2>'+esc(section['heading'])+'</h2>')
  blocks.append(paragraphs(section.get('text')))
 blocks.append(paragraphs(article.get('cta')))
 if article.get('tags'):blocks.append('<p class="tags">'+esc(' '.join('#'+str(t) for t in article['tags']))+'</p>')
 blocks.append('</article><section class="pictures">')
 assets=[]
 for index,img in enumerate(data.get('images',[]),1):
  value=_export_image_bytes(img)
  if value:
   content,mime,suffix=value
   filename=f'images/image-{index}{suffix}'
   assets.append((filename,content))
   src='data:'+mime+';base64,'+base64.b64encode(content).decode('ascii') if embedded else filename
   blocks.append('<figure><img src="'+esc(src)+'" alt="'+esc(img.get('caption'))+'"><figcaption>'+esc(img.get('caption'))+'</figcaption></figure>')
  elif img.get('source_url'):
   blocks.append('<p><a href="'+esc(img['source_url'])+'">打开第'+str(index)+'张配图的来源</a></p>')
 sources_label='核验使用的资料' if data.get('mode')=='verified_edit' else '产品事实与出处'
 blocks.append('</section><details><summary>'+sources_label+'</summary><ul>')
 for fact in data.get('facts',[]):
  source=str(fact.get('source_url',''))
  link='<a href="'+esc(source)+'">'+esc(fact.get('source_title','来源'))+'</a>' if source.startswith('https://') else esc(fact.get('source_title',''))
  blocks.append('<li><strong>'+esc(fact.get('text'))+'</strong><br>'+esc(fact.get('quote'))+'<br>'+link+'</li>')
 blocks.append('</ul></details><footer>'+esc(data.get('generated_at'))+' · '+esc(data.get('run_id'))+'</footer>')
 css='body{font-family:STKaiti,KaiTi,AnthropicSerifWebText,Georgia,serif;max-width:860px;margin:48px auto;padding:0 24px;color:#292b31;background:#fffefa;line-height:1.85}h1{font-size:34px;line-height:1.35}h2{font-size:24px}p{white-space:pre-wrap}header{border-bottom:1px solid #dbd4c9;padding-bottom:20px;margin-bottom:28px}.brand{color:#074591;letter-spacing:.16em}figure{margin:30px 0}img{max-width:100%;height:auto;border-radius:8px}figcaption,footer{font-size:14px;color:#62626b}details{border-top:1px solid #dbd4c9;margin-top:30px;padding:20px 0}li{margin:16px 0}a{color:#074591;overflow-wrap:anywhere}footer{margin-top:30px}@media print{body{background:white;margin:15mm auto}details{display:block}img{max-height:220mm}}'
 document='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+esc(article.get('title'))+'</title><style>'+css+'</style><body>'+''.join(blocks)+'</body></html>'
 return document,assets

@app.get('/api/runs/{run_id}/export')
def export_run(run_id:str,format:str='md'):
 if format not in ('md','json','html','zip'):raise HTTPException(400,'支持 md、json、html 或 zip 格式。')
 if not restore_run(run_id):examples()
 data=_runs.get(run_id)
 if data is None:raise HTTPException(404,'运行记录不存在。')
 if format in ('html','zip'):
  if data.get('status') not in ('success','ok'):raise HTTPException(409,'此记录尚无通过核对的成稿，请先处理待确认项。')
  document,assets=render_html_export(data,embedded=format=='html')
  if format=='html':return Response(document,media_type='text/html; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="zhi-hua-{run_id[:8]}.html"'})
  local_images={}
  for filename,_ in assets:
   image_index=int(re.fullmatch(r'images/image-(\d+)\.[a-zA-Z0-9]+',filename).group(1))-1
   local_images[str(data['images'][image_index].get('url',''))]=filename
  markdown=re.sub(r'(!\[[^\]\n]*\]\()([^\)\n]+)(\))',
                  lambda match:match[1]+local_images.get(match[2],match[2])+match[3],data.get('markdown',''))
  output=io.BytesIO()
  with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
   archive.writestr('图文.html',document)
   archive.writestr('稿件.md',markdown)
   for name,content in assets:archive.writestr(name,content)
  return Response(output.getvalue(),media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="zhi-hua-{run_id[:8]}.zip"'})
 if data.get('status') not in ('success','ok'):
  report={key:data[key] for key in ('status','message','run_id','generated_at','input','questions','facts','checks','trace') if key in data}
  content=json.dumps(report,ensure_ascii=False,indent=2) if format=='json' else '# 知华·有据 · 任务核验记录\n\n此记录不包含未通过核验的成稿正文。\n\n'+str(report.get('message',''))+'\n\n'+'\n'.join('- ['+str(c.get('status',''))+'] '+str(c.get('type',''))+'：'+str(c.get('claim',''))+'；'+str(c.get('action','')) for c in report.get('checks',[]))
 else:content=json.dumps(data,ensure_ascii=False,indent=2) if format=='json' else data.get('markdown','')
 return Response(content,media_type='application/json' if format=='json' else 'text/markdown; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="zhi-hua-{run_id[:8]}.{format}"'})

if __name__=='__main__':
 import uvicorn
 uvicorn.run(app,host='0.0.0.0',port=int(os.getenv('PORT','7860')),log_level='warning')

