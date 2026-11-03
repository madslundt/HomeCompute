import importlib.util
from pathlib import Path
import tarfile
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
s=importlib.util.spec_from_file_location('cli_build',ROOT/'scripts/build-openclaw-managed-cli.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)

class BuildTests(unittest.TestCase):
    def test_global_install_and_onboarding_are_absent_from_reviewed_build_recipe(self):
        for command in m.commands():
            self.assertNotIn('link',command);self.assertNotIn('onboard',command)
            if 'ci' in command:self.assertIn('--ignore-scripts',command)
        self.assertEqual(set(m.VERSIONS),{'0.0.129','0.0.140'})

    def test_unpublished_source_cannot_prepare_any_vendor_work(self):
        with self.assertRaises(ValueError):m.source_guard('a'*40,m.bundle())
        with self.assertRaises(ValueError):m.source_guard('--all',m.bundle())

    def test_archive_is_reproducible_excludes_git_and_refuses_external_links(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'source';source.mkdir();(source/'public.js').write_text('synthetic public code')
            (source/'.git').mkdir();(source/'.git/private-cache').write_text('not part of runtime bundle')
            first=root/'first.gz';second=root/'second.gz'
            self.assertEqual(m.archive(source,first,'cli'),m.archive(source,second,'cli'))
            with tarfile.open(first) as archive:
                self.assertNotIn('cli/.git',archive.getnames())
                self.assertTrue(all(info.uid==0 and info.gid==0 and info.mtime==0 for info in archive))
            (source/'external').symlink_to(root/'first.gz')
            with self.assertRaisesRegex(ValueError,'escapes'):m.archive(source,root/'third.gz','cli')

if __name__=='__main__':unittest.main()
