#!/usr/bin/env python3
"""Exercise real Ansible selection tasks with synthetic read-only reports."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
plays = []


def play(name, tasks, variables=None):
    plays.append(dict(name=name, hosts='localhost', connection='local',
                      gather_facts=False, tasks=([{'ansible.builtin.set_fact': variables}] if variables else []) + tasks))


def report(section, key, values):
    return {'stdout': json.dumps({'report': [{section: [{key: v} for v in values]}]})}


for name, vgs, lvs, expected in [
    ('empty server', [], [], ''),
    ('second disk', ['vg_data'], ['lv_data'], '-1'),
    ('third disk', ['vg_data', 'vg_data-1'], ['lv_data', 'lv_data-1'], '-2'),
    ('gap reused', ['vg_data', 'vg_data-2'], ['lv_data', 'lv_data-2'], '-1'),
    ('LV collision in another VG', [], ['lv_data'], '-1'),
    ('different occupied suffixes', ['vg_data', 'vg_data-1'], ['lv_data-2'], '-3'),
]:
    play(name, [
        {'ansible.builtin.include_tasks': str(ROOT / 'tasks/select_names.yml')},
        {'ansible.builtin.assert': {'that': [
            "create_disk_selected_vg == 'vg_data" + expected + "'",
            "create_disk_selected_lv == 'lv_data" + expected + "'",
        ]}},
    ], dict(create_disk_vg_base='vg_data', create_disk_lv_base='lv_data',
            create_disk_selected_vg='stale_fact_from_previous_invocation',
            create_disk_lvm_reports={'results': [report('vg', 'vg_name', vgs),
                report('lv', 'lv_name', lvs), report('pv', 'pv_name', [])]}))

base = dict(name='/dev/sdb', type='disk', ro=False, mountpoints=[None])
facts = dict(partitions={}, holders=[], removable='0', sectors='1048576')
for name, devices, fact, pvs, explicit, expected in [
    ('single disk', [base], facts, [], '', '/dev/sdb1'),
    ('NVMe partition suffix', [{**base, 'name': '/dev/nvme0n1'}], facts, [], '', '/dev/nvme0n1p1'),
    ('no disks', [], facts, [], '', None),
    ('two disks ambiguous', [base, {**base, 'name': '/dev/sdc'}], facts, [], '', None),
    ('explicit selection', [base, {**base, 'name': '/dev/sdc'}], facts, [], '/dev/sdc', '/dev/sdc1'),
    ('mounted whole disk', [{**base, 'mountpoints': ['/data']}], facts, [], '', None),
    ('read only', [{**base, 'ro': True}], facts, [], '', None),
    ('existing partitions', [{**base, 'children': [{'name': '/dev/sdb1'}]}], facts, [], '', None),
    ('existing whole disk PV', [base], facts, ['/dev/sdb'], '', None),
    ('device mapper holder', [base], {**facts, 'holders': ['dm-0']}, [], '', None),
    ('removable', [base], {**facts, 'removable': '1'}, [], '', None),
    ('missing facts', [base], {}, [], '', None),
    ('explicit unsafe PV', [base], facts, ['/dev/sdb'], '/dev/sdb', None),
]:
    tasks = [
        {'ansible.builtin.set_fact': {'selection_failed': False}},
        {'block': [{'ansible.builtin.include_tasks': str(ROOT / 'tasks/select_device.yml')}],
         'rescue': [{'ansible.builtin.set_fact': {'selection_failed': True}}]},
        {'ansible.builtin.assert': {'that': [f'selection_failed == {expected is None}']}},
    ]
    if expected:
        tasks.append({'ansible.builtin.assert': {'that': [
            'create_disk_selected_partition == ' + json.dumps(expected)]}})
    play(name, tasks, dict(create_disk_lsblk={'stdout': json.dumps({'blockdevices': devices})},
        ansible_facts={'devices': {d['name'].split('/')[-1]: fact for d in devices}},
        create_disk_existing_pvs=pvs, create_disk_device=explicit))

play('existing mount destination stops the real role before mutation', [
    {'ansible.builtin.set_fact': {'guard_failed': False}},
    {'block': [{'ansible.builtin.include_role': {'name': str(ROOT)}, 'vars': {'mount_point': '/tmp'}}],
     'rescue': [
         {'ansible.builtin.assert': {'that': [
             "'already exists' in ansible_failed_result.msg"]}},
         {'ansible.builtin.set_fact': {'guard_failed': True}},
     ]},
    {'ansible.builtin.assert': {'that': 'guard_failed'}},
])
with tempfile.TemporaryDirectory(prefix='create-disk-tests-') as temp:
    path = Path(temp) / 'cases.json'
    path.write_text(json.dumps(plays))
    env = dict(os.environ, ANSIBLE_HOME=temp, ANSIBLE_LOCAL_TEMP=temp + '/local',
               ANSIBLE_REMOTE_TEMP=temp + '/remote')
    syntax = Path(temp) / 'all-tasks.json'
    syntax.write_text(json.dumps([dict(hosts='localhost', gather_facts=False, tasks=[
        {'ansible.builtin.import_tasks': str(p)}
        for p in sorted((ROOT / 'tasks').glob('*.yml'))
    ])]))
    subprocess.run(['ansible-playbook', '-i', 'localhost,', str(syntax), '--syntax-check'],
                   env=env, check=True)
    command = ['ansible-playbook', '-i', 'localhost,', str(path)]
    subprocess.run(command + ['--syntax-check'], env=env, check=True)
    subprocess.run(command, env=env, check=True)
print(f'{len(plays)} Ansible scenarios passed; no storage commands executed.')
