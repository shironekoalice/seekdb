import sys,json,traceback
from pathlib import Path
root=Path('/mnt/e/develop/project_files/project/oceanbase/worktrees/minivectordb-f03')
sys.path.insert(0,str(root/'protocol_check'))
from _transport import Session
output=Path('/mnt/e/develop/project_files/project/oceanbase/minivectordb/artifacts/requirements-audit-f03-20261114-1102')
rows=[]
for length in (248,260):
    s=Session(json.loads((root/'protocol_check/config.json').read_text()),output/f'long-name-{length}.log',command=['/opt/mvdb-f03-release/minivectordb','--data-dir','{data_dir}'])
    row={'length':length,'target_binary':'fbcb91479d1ed98dfcbb639f23bba4cdea3b23f4f47ec24affb5e391c4ba198c'}
    try:
        s.start(); name='t'+'a'*(length-1)
        row['create']=s.request(f'CREATE TABLE {name}(id BIGINT PRIMARY KEY);')
        row['insert']=s.request(f'INSERT INTO {name} VALUES(1);')
        row['select']=s.request(f'SELECT id FROM {name};')
        s.shutdown()
    except Exception as exc:
        row['error']=str(exc)
        s.stop()
        try: s.start();row['restart_ready']=True
        except Exception as second: row['restart_error']=str(second)
    finally:s.close()
    rows.append(row)
(output/'long-names-control.json').write_text(json.dumps(rows,indent=2)+'\n')
print(json.dumps(rows,indent=2))
