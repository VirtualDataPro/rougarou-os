import importlib.util,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('checkpoint',ROOT/'deployment/proxmox-checkpoint.py'); cp=importlib.util.module_from_spec(spec);spec.loader.exec_module(cp)

class CheckpointGuards(unittest.TestCase):
    def test_invalid_ids_cannot_reach_hypervisor(self):
        with patch.object(cp,'call') as call:
            for vmid in ['99','1000000000']:
                with self.assertRaises(ValueError): cp.validate_id(vmid)
            call.assert_not_called()
    def test_generic_helper_has_no_hardcoded_lab_ids(self):
        self.assertEqual(cp.validate_id('200'),200)
        self.assertEqual(cp.validate_id('201'),201)
    def test_cloud_network_configuration_is_bound(self):
        from types import SimpleNamespace
        base='name: test\nnet0: virtio=02:00:00:00:00:01\nciuser: alice\nipconfig0: ip=192.0.2.4/24\n'
        with patch.object(cp,'call',return_value=SimpleNamespace(stdout=base)):
            before=cp.config(204)
        with patch.object(cp,'call',return_value=SimpleNamespace(stdout=base.replace('192.0.2.4','192.0.2.5').replace('alice','bob'))):
            after=cp.config(204)
        self.assertNotEqual(before,after)
        self.assertIn('ipconfig0',before)
    def test_public_seed_keys_are_hashed_not_exported(self):
        from types import SimpleNamespace
        data='name: test\nnet0: virtio=02:00:00:00:00:01\nsshkeys: PUBLICKEYBYTES\n'
        with patch.object(cp,'call',return_value=SimpleNamespace(stdout=data)):
            result=cp.config(204)
        self.assertNotIn('PUBLICKEYBYTES',json.dumps(result))
        self.assertEqual(len(result['cloud_public_keys_sha256']),64)
    def test_password_seed_checkpoint_is_refused(self):
        from types import SimpleNamespace
        data='name: test\nnet0: virtio=02:00:00:00:00:01\ncipassword: SECRET\n'
        with patch.object(cp,'call',return_value=SimpleNamespace(stdout=data)):
            with self.assertRaises(ValueError):cp.config(204)
    def record(self,root):
        path=root/'record.json'
        cp.save(path,{'schema':1,'vmid':204,'node':cp.os.uname().nodename,'config':{'name':'rougarou-cloud-test','net0':'virtio=02:00:00:00:00:01','scsi0':'test-storage:vm-204-disk-0'},'identity':{'machine_id':'abc'},'snapshot':'before','snapshot_receipt':{'snaptime':'123','description':'test-token'}},create=True)
        return path
    def test_private_record_and_exact_vm_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=self.record(Path(tmp));self.assertEqual(cp.read_record(p,204)['vmid'],204)
            with self.assertRaises(ValueError): cp.read_record(p,205)
            p.chmod(0o644)
            with self.assertRaises(ValueError): cp.read_record(p,204)
    def test_symlink_record_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=self.record(Path(tmp)); link=Path(tmp)/'link';link.symlink_to(p)
            with self.assertRaises(ValueError): cp.read_record(link,204)
    def test_restore_refuses_changed_disk_or_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=self.record(Path(tmp));rec=cp.read_record(p,204)
            with patch.object(cp,'read_record',return_value=rec),patch.object(cp.os,'geteuid',return_value=0),patch.object(cp,'config',return_value={'name':'different'}),patch.object(cp,'call') as call:
                with self.assertRaises(ValueError): cp.main(['restore','204','--record',str(p),'--snapshot','before','--confirm','204:before'])
                call.assert_not_called()
    def test_restore_never_runs_on_running_vm(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=self.record(Path(tmp));rec=cp.read_record(p,204)
            with patch.object(cp,'read_record',return_value=rec),patch.object(cp.os,'geteuid',return_value=0),patch.object(cp,'config',return_value=rec['config']),patch.object(cp,'status',return_value='running'),patch.object(cp,'call') as call:
                with self.assertRaises(ValueError): cp.main(['restore','204','--record',str(p),'--snapshot','before','--confirm','204:before'])
                call.assert_not_called()
    def test_wrong_confirmation_cannot_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=self.record(Path(tmp));rec=cp.read_record(p,204)
            with patch.object(cp,'read_record',return_value=rec),patch.object(cp.os,'geteuid',return_value=0),patch.object(cp,'config',return_value=rec['config']),patch.object(cp,'call') as call:
                with self.assertRaises(ValueError): cp.main(['restore','204','--record',str(p),'--snapshot','before','--confirm','205:before'])
                call.assert_not_called()
    def test_replaced_snapshot_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=self.record(Path(tmp));rec=cp.read_record(p,204)
            with patch.object(cp,'read_record',return_value=rec),patch.object(cp.os,'geteuid',return_value=0),patch.object(cp,'config',return_value=rec['config']),patch.object(cp,'status',return_value='stopped'),patch.object(cp,'snapshot_receipt',return_value={'snaptime':'456','description':'new-token'}),patch.object(cp,'call') as call:
                with self.assertRaises(ValueError): cp.main(['restore','204','--record',str(p),'--snapshot','before','--confirm','204:before'])
                call.assert_not_called()
    def test_restore_leaves_guest_stopped(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=self.record(Path(tmp));rec=cp.read_record(p,204)
            with patch.object(cp,'read_record',return_value=rec),patch.object(cp.os,'geteuid',return_value=0),patch.object(cp,'config',return_value=rec['config']),patch.object(cp,'status',return_value='stopped'),patch.object(cp,'snapshot_receipt',return_value=rec['snapshot_receipt']),patch.object(cp,'call') as call:
                cp.main(['restore','204','--record',str(p),'--snapshot','before','--confirm','204:before'])
                call.assert_called_once_with(['qm','rollback','204','before','--start','0'])

if __name__=='__main__':unittest.main()
