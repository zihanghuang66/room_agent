"""Tests only temporary fixture hosts files; never changes this Mac's resolver."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
import json
from unittest.mock import patch

SOURCE=Path(__file__).resolve().parents[1]/'codex-dns-repair-v0.1.0/repair.py'


class HostsTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SOURCE.is_file(),'DNS repair implementation missing')
        spec=importlib.util.spec_from_file_location('dns_repair',SOURCE)
        self.module=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.module)

    def test_preserve_other_hosts_and_exact_rollback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);hosts=root/'hosts';run=root/'run';run.mkdir()
            original=b'127.0.0.1 localhost\n104.244.46.57 chatgpt.com other.example # alias\n10.0.0.1 school.internal\n'
            hosts.write_bytes(original);inode=hosts.stat().st_ino
            state=self.module.install_override(hosts,run,'104.18.32.47')
            new=hosts.read_text()
            self.assertIn('10.0.0.1 school.internal\n',new)
            self.assertIn('104.244.46.57 other.example # alias',new)
            self.assertIn('104.18.32.47 chatgpt.com',new)
            self.assertEqual(hosts.stat().st_ino,inode,'Must support a bind-mounted hosts file')
            self.module.restore(hosts,run,state)
            self.assertEqual(hosts.read_bytes(),original)

    def test_rollback_refuses_to_overwrite_later_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);hosts=root/'hosts';run=root/'run';run.mkdir();hosts.write_text('127.0.0.1 localhost\n')
            state=self.module.install_override(hosts,run,'104.18.32.47')
            hosts.write_text(hosts.read_text()+'10.0.0.2 platform-added\n')
            with self.assertRaisesRegex(RuntimeError,'changed'):self.module.restore(hosts,run,state)
            self.assertIn('platform-added',hosts.read_text())

    def test_refresh_does_not_duplicate_entries(self):
        original=b'127.0.0.1 localhost\n'
        one=self.module.render_hosts(original,'104.18.32.47')
        two=self.module.render_hosts(one,'172.64.155.209')
        self.assertEqual(two.count(b'chatgpt.com'),1)
        self.assertNotIn(b'104.18.32.47',two)

    def test_only_public_ipv4_answers_are_accepted(self):
        data={'Status':0,'Answer':[{'type':1,'data':'127.0.0.1'},{'type':1,'data':'104.18.32.47'},
                                  {'type':1,'data':'10.0.0.1'},{'type':5,'data':'alias.example'}]}
        self.assertEqual(self.module.addresses(data),['104.18.32.47'])
        with self.assertRaises(ValueError):self.module.addresses({'Status':2})

    def test_failed_post_check_restores_hosts_and_returns_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);hosts=root/'hosts';hosts.write_text('127.0.0.1 localhost\n')
            before=hosts.read_bytes()
            def dns(cmd):
                Path(cmd[cmd.index('--output')+1]).write_text(json.dumps({'Status':0,'Answer':[{'type':1,'data':'104.18.32.47','TTL':60}]}))
                return 0,'200',''
            good={'exit':0,'http':'403','remote':'104.18.32.47','error':'','transport_ok':True}
            bad={'exit':28,'http':'000','remote':'','error':'timeout','transport_ok':False}
            with patch.object(self.module,'HOSTS',hosts),patch.object(self.module,'RET',root/'return_output'), \
                 patch.object(self.module.os,'geteuid',return_value=0),patch.dict(self.module.os.environ,{},clear=True), \
                 patch.object(self.module.sys,'argv',['repair.py']),patch.object(self.module,'call',side_effect=dns), \
                 patch.object(self.module,'probe',side_effect=[good,bad]):
                self.assertEqual(self.module.main(),1)
            self.assertEqual(hosts.read_bytes(),before)
            reports=list((root/'return_output/environment_reports').glob('*/report.json'))
            self.assertEqual(json.loads(reports[0].read_text())['auto_rollback'],'ok')
            self.assertEqual(len(list((root/'return_output').glob('*.tar.gz'))),1)

if __name__=='__main__':unittest.main()
