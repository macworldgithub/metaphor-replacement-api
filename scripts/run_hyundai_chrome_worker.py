"""Read and write through the existing authenticated Chrome extension session.

No password/TOTP, profile cloning or MFA bypass. Without an extension token,
Chrome's normal share-tab prompt must be accepted by the operator.
"""
import argparse
import asyncio
import json
import os
import shutil
from datetime import timedelta
from pathlib import Path
from dotenv import load_dotenv
import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT=Path(__file__).resolve().parents[1]

def browser_child_environment(environment):
    private_prefixes=('DEALERSOCKET_','MOBILEMESSAGE_','PORTAL_','LEAD_','MONGODB_','GOODSHOWROOM_','SMS_','CAMPAIGN_')
    return {key:value for key,value in environment.items() if not key.startswith(private_prefixes)}


def result_value(result):
    text='\n'.join(block.text for block in result.content if getattr(block,'type',None)=='text')
    if result.isError or '### Error' in text:
        first=text.split('### Error',1)[-1].strip().split('\n')[0][:500]
        raise RuntimeError('Chrome automation failed: '+first)
    return json.loads(text.split('### Result\n',1)[-1].split('\n### ',1)[0].strip())

def save_journal(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp')
    with temporary.open('w',encoding='utf-8') as file:
        json.dump(value,file);file.flush();os.fsync(file.fileno())
    os.replace(temporary,path)

async def main(once, prospects=False):
    load_dotenv(ROOT.parent/'sms-campaign-backend/.env')
    load_dotenv(ROOT/'.env')
    key=os.getenv('PORTAL_API_KEY','')
    if len(key)<32:raise RuntimeError('Configure the backend portal key before starting the Chrome worker.')
    local_cli=os.getenv('PLAYWRIGHT_MCP_CLI','')
    command=shutil.which('node') if local_cli else shutil.which('npx.cmd' if os.name=='nt' else 'npx')
    if not command:raise RuntimeError('Node.js is required.')
    output=ROOT/'.private/hyundai-chrome-worker'
    output.mkdir(parents=True,exist_ok=True)
    lease=(output/'worker.lock').open('a+b')
    if os.fstat(lease.fileno()).st_size==0:lease.write(b'0');lease.flush()
    lease.seek(0)
    if os.name=='nt':
        import msvcrt
        msvcrt.locking(lease.fileno(),msvcrt.LK_NBLCK,1)
    else:
        import fcntl
        fcntl.flock(lease.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    environment=browser_child_environment(os.environ)
    params=StdioServerParameters(command=command,args=([local_cli] if local_cli else ['-y','@playwright/mcp@0.0.83'])+['--extension','--browser','chrome','--output-dir',str(output)],cwd=str(ROOT),env=environment)
    print('Connecting to authenticated Chrome. Accept its share-tab prompt if shown.',flush=True)
    async with httpx.AsyncClient(base_url=os.getenv('CAMPAIGN_BACKEND_URL','http://127.0.0.1:4000'),headers={'X-Portal-Key':key},timeout=30,transport=httpx.AsyncHTTPTransport(retries=5)) as api:
        async with stdio_client(params) as(read,write):
            async with ClientSession(read,write,read_timeout_seconds=timedelta(seconds=900 if prospects else 240)) as session:
                await session.initialize()
                print('Chrome automation client initialized.',flush=True)
                names={tool.name for tool in (await session.list_tools()).tools}
                tool=next((name for name in ('browser_run_code_unsafe','browser_run_code') if name in names),None)
                if not tool:raise RuntimeError('The Chrome MCP server has no supported Playwright code tool.')
                attached=await session.call_tool('browser_tabs',{'action':'new','url':'https://bb.dealersocket.com/#/crm/employee?todoType=opportunities&dealershipId=10876&filterId=36'})
                if attached.isError:raise RuntimeError('Chrome tab sharing was not completed.')
                print('DealerSocket tab attached.',flush=True)
                if prospects:
                    code = (ROOT / 'scripts/read_south_morang_prospects.js').read_text(encoding='utf-8-sig')
                    snapshot = result_value(await session.call_tool(tool, {'code': code}))
                    response = await api.post('/api/worker/chrome/south-morang-prospects', json=snapshot)
                    response.raise_for_status()
                    print(f"South Morang prospects saved: {response.json()['saved']}", flush=True)
                    return
                next_read=0
                catalog_sent=False
                while True:
                    if asyncio.get_running_loop().time()>=next_read:
                        try:
                            snapshot=result_value(await session.call_tool(tool,{'code':(ROOT/'scripts/read_south_morang_chrome.js').read_text(encoding='utf-8')}))
                        except RuntimeError as error:
                            if once:raise
                            response=await api.post('/api/worker/chrome/claim',json={'authenticated':False});response.raise_for_status()
                            print(str(error)+'. Read paused; keeping the Chrome connection for recovery.',flush=True)
                            snapshot=None
                            next_read=asyncio.get_running_loop().time()+60
                        if snapshot is not None:
                            response=await api.post('/api/worker/chrome/snapshot',json=snapshot);response.raise_for_status()
                            print(f'Hyundai snapshot refreshed: {len(snapshot["records"])} scoped records.',flush=True)
                            if not catalog_sent and snapshot['records']:
                                try:
                                    code=(ROOT/'scripts/read_south_morang_catalog.js').read_text(encoding='utf-8').replace('__CONTACT_ID__',json.dumps(snapshot['records'][0]['customer_id']))
                                    catalog=result_value(await session.call_tool(tool,{'code':code}))
                                    response=await api.post('/api/worker/chrome/catalog',json=catalog);response.raise_for_status();catalog_sent=True
                                    print('Hyundai assignee and franchise catalog refreshed without saving a CRM record.',flush=True)
                                except Exception:print('Assignee catalog refresh deferred; existing verified options retained.',flush=True)
                            next_read=asyncio.get_running_loop().time()+max(300,int(os.getenv('DEALERSOCKET_LEAD_SYNC_INTERVAL_SECONDS','1800')))
                    if once:return
                    check=result_value(await session.call_tool(tool,{'code':"async(page)=>{let authenticated=page.url().startsWith('https://bb.dealersocket.com/')&&await page.locator('iframe#app-crm').isVisible()&&await page.locator('input[type=password]:visible').count()===0;if(authenticated){const selector=page.locator('md-select#select_1 .md-select-value,md-select#select_1 md-select-value').first();if((await selector.innerText()).trim()!=='South Morang Hyundai'){await page.locator('md-select#select_1').click();await page.getByRole('option',{name:'South Morang Hyundai',exact:true}).click();await page.waitForURL('**/#/homepage',{timeout:30000});await page.locator('#app-loading-overlay').waitFor({state:'hidden'});await page.goto('https://bb.dealersocket.com/#/crm/employee?todoType=opportunities&dealershipId=10876&filterId=36');await page.locator('iframe#app-crm').waitFor({state:'visible'});}authenticated=(await selector.innerText()).trim()==='South Morang Hyundai';}return{authenticated};}"}))
                    response=await api.post('/api/worker/chrome/claim',json=check);response.raise_for_status();job=response.json()
                    if job.get('refreshRequested'):next_read=0
                    if job.get('leadId'):
                        path=output/'journal'/(job['leadId']+'.json')
                        saved=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
                        if job.get('journalResetRequested'):
                            saved={}
                            save_journal(path,{'state':'preparing','leadId':job['leadId'],'operatorReconciled':True})
                        if saved.get('state') in ('saving','needs_review','synced') or job.get('reconcile'):
                            result=saved if saved.get('state')=='synced' else {'state':'needs_review'}
                        else:
                            save_journal(path,{'state':'preparing','leadId':job['leadId']})
                            attempting_save=False
                            try:
                                if not job.get('contactId'):
                                    code=(ROOT/'scripts/match_south_morang_customer.js').read_text(encoding='utf-8').replace('__CONTACT_LOOKUP__',json.dumps({'mobile':job['mobile'],'email':job.get('email','')}))
                                    match=result_value(await session.call_tool(tool,{'code':code}))
                                    if match.get('state')=='matched':job['contactId']=match['contactId']
                                if not job.get('contactId'):
                                    result={'state':'needs_match'}
                                else:
                                    attempting_save=True
                                    save_journal(path,{'state':'saving','leadId':job['leadId']})
                                    code=(ROOT/'scripts/write_south_morang_chrome.js').read_text(encoding='utf-8').replace('__CAMPAIGN_LEAD__',json.dumps(job))
                                    result=result_value(await session.call_tool(tool,{'code':code}));result['contactId']=str(job['contactId'])
                            except Exception:
                                result={'state':'needs_review' if attempting_save else 'needs_match'}
                            save_journal(path,result)
                        response=await api.post('/api/worker/chrome/results/'+job['leadId'],json=result);response.raise_for_status()
                        print('CRM job completed: '+result['state'],flush=True)
                    if os.getenv('SMS_CHANNEL_MODE','one_way')=='two_way' and os.getenv('DEALERSOCKET_SMS_ACTIVITY_ENABLED')=='true':
                        response=await api.post('/api/worker/chrome/sms/claim',json=check);response.raise_for_status();activity=response.json()
                        if activity.get('id'):
                            path=output/'sms-journal'/(activity['id']+'.json')
                            saved=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
                            if saved.get('state') in ('saving','needs_review','synced') or activity.get('reconcile'):
                                result=saved if saved.get('state')=='synced' else {'state':'needs_review'}
                            elif not activity.get('contactId') or not activity.get('opportunityId'):
                                result={'state':'needs_match'}
                            else:
                                save_journal(path,{'state':'saving','id':activity['id']})
                                try:
                                    code=(ROOT/'scripts/write_south_morang_sms_activity.js').read_text(encoding='utf-8').replace('__SMS_ACTIVITY__',json.dumps(activity))
                                    result=result_value(await session.call_tool(tool,{'code':code}))
                                except Exception:result={'state':'needs_review'}
                                save_journal(path,result)
                            response=await api.post('/api/worker/chrome/sms/results/'+activity['id'],json=result);response.raise_for_status()
                            print('SMS activity job completed: '+result['state'],flush=True)
                    await asyncio.sleep(5)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--once',action='store_true')
    parser.add_argument('--prospects', action='store_true', help='Refresh the South Morang active prospect snapshot once')
    args=parser.parse_args()
    try:asyncio.run(main(args.once, args.prospects))
    except KeyboardInterrupt:pass
    except Exception as error:
        def leaves(value):
            for child in getattr(value,'exceptions',[]):
                yield from leaves(child)
            if not getattr(value,'exceptions',None):yield value
        details=[]
        for value in leaves(error):
            message=str(value)
            for key,secret in os.environ.items():
                if len(secret)>5 and any(word in key for word in ('PASSWORD','SECRET','TOKEN','API_KEY','CONNECTOR_KEY')):message=message.replace(secret,'[redacted]')
            details.append(type(value).__name__+': '+message[:350])
        print('Chrome worker stopped. '+ '; '.join(details),flush=True)
        raise SystemExit(1)
