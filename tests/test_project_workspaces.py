"""Real local Git fixtures: no GitHub/network and no automatic repository code."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from lodestar import project_workspaces as git
from lodestar.project_sources import RepositoryError


class WorkspaceGitTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix='lodestar-git-')
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name); self.key='b'*32
        self.path=git.workspace_path(self.root,self.key)
        self.path.mkdir(parents=True)
        self.run_git('init','--quiet','-b','master')
        self.run_git('config','user.name','Offline fixture')
        self.run_git('config','user.email','fixture@example.invalid')
        self.run_git('commit','--quiet','--allow-empty','-m','initial fixture')
        self.base=self.run_git('rev-parse','HEAD').strip()
        self.run_git('remote','add','origin','https://github.com/example/research')

    def run_git(self,*args):
        return git._run(self.path,*args)

    def snapshot(self,sha):
        return {'repository':'https://github.com/example/research','branch':'master','commit_sha':sha}

    def fetched(self,sha):
        original=git._run
        def transport(directory,*args,**kwargs):
            if args[0]=='fetch':
                # Exercise real Git's FETCH_HEAD using only this local fixture.
                value=subprocess.run(['git','-C',str(directory),'-c','protocol.file.allow=always',
                    '-c','core.hooksPath='+os.devnull,'fetch','--quiet','.',sha],
                    capture_output=True,text=True,timeout=10,check=True)
                return value.stdout
            return original(directory,*args,**kwargs)
        return patch('lodestar.project_workspaces._run',side_effect=transport)

    def test_actual_status_reports_local_changes_without_exposing_file_content(self):
        (self.path/'draft.txt').write_text('PRIVATE_CODE_CANARY',encoding='utf-8')
        value=git.status(self.root,self.key)
        self.assertTrue(value['dirty']); self.assertEqual(value['branch'],'master')
        self.assertNotIn('PRIVATE_CODE_CANARY',str(value))

    def test_dirty_worktree_never_fetches_or_overwrites(self):
        (self.path/'draft.txt').write_text('keep me',encoding='utf-8')
        with patch('lodestar.project_workspaces.credential',side_effect=AssertionError('no network credentials')):
            with self.assertRaises(RepositoryError): git.sync(self.root,self.key,self.snapshot(self.base))
        self.assertEqual((self.path/'draft.txt').read_text(encoding='utf-8'),'keep me')

    def test_clean_fast_forward_uses_git_and_does_not_run_post_merge_hook(self):
        self.run_git('checkout','--quiet','-b','fixture-remote')
        (self.path/'README.md').write_text('new source',encoding='utf-8')
        self.run_git('add','README.md'); self.run_git('commit','--quiet','-m','remote fixture')
        sha=self.run_git('rev-parse','HEAD').strip(); self.run_git('checkout','--quiet','master')
        marker=self.root/'HOOK_MUST_NOT_RUN'
        hook=self.path/'.git/hooks/post-merge'
        hook.write_text('#!/bin/sh\ntouch "'+marker.as_posix()+'"\n',encoding='utf-8'); hook.chmod(0o755)
        with self.fetched(sha),patch('lodestar.project_workspaces.credential',return_value=None):
            result=git.sync(self.root,self.key,self.snapshot(sha))
        self.assertEqual(result['commit_sha'],sha); self.assertFalse(result['dirty'])
        self.assertEqual((self.path/'README.md').read_text(encoding='utf-8'),'new source')
        self.assertFalse(marker.exists())

    def test_divergence_keeps_local_commit_and_refuses_merge(self):
        self.run_git('checkout','--quiet','-b','fixture-remote')
        self.run_git('commit','--quiet','--allow-empty','-m','remote change')
        remote=self.run_git('rev-parse','HEAD').strip(); self.run_git('checkout','--quiet','master')
        self.run_git('commit','--quiet','--allow-empty','-m','local change')
        local=self.run_git('rev-parse','HEAD').strip()
        with self.fetched(remote),patch('lodestar.project_workspaces.credential',return_value=None):
            with self.assertRaises(RepositoryError): git.sync(self.root,self.key,self.snapshot(remote))
        self.assertEqual(self.run_git('rev-parse','HEAD').strip(),local)

    def test_changed_branch_origin_and_out_of_root_keys_are_rejected(self):
        for key in ('../bad','C:\\outside','a'*31):
            with self.assertRaises(ValueError): git.workspace_path(self.root,key)
        self.run_git('checkout','--quiet','-b','my-local-branch')
        with self.assertRaises(RepositoryError): git.sync(self.root,self.key,self.snapshot(self.base))
        self.run_git('checkout','--quiet','master')
        self.run_git('remote','set-url','origin','https://github.com/other/repo')
        with self.assertRaises(RepositoryError): git.sync(self.root,self.key,self.snapshot(self.base))

    def test_auth_is_ephemeral_and_hooks_filters_global_config_are_disabled(self):
        with patch('subprocess.run',return_value=subprocess.CompletedProcess([],0,'','')) as child:
            git._run(self.path,'fetch','--quiet','origin','master',network=True,token='offline-credential-canary')
        args=child.call_args.args[0]; env=child.call_args.kwargs['env']
        self.assertNotIn('offline-credential-canary',' '.join(args))
        self.assertEqual(env['GIT_CONFIG_GLOBAL'],os.devnull)
        self.assertEqual(env['GIT_TERMINAL_PROMPT'],'0')
        self.assertIn('core.hooksPath='+os.devnull,args)
        self.assertIn('filter.lfs.process=',args)
        self.assertTrue(env['GIT_CONFIG_VALUE_0'].startswith('Authorization: Basic '))

    def test_initial_clone_creates_a_real_editable_checkout_without_external_network(self):
        original=git._run
        def transport(directory,*args,**kwargs):
            if args[0]=='clone':
                command=['git','-c','protocol.file.allow=always','-c','core.hooksPath='+os.devnull,
                         *args[:-2],str(self.path),args[-1]]
                return subprocess.run(command,capture_output=True,text=True,timeout=10,check=True).stdout
            return original(directory,*args,**kwargs)
        with patch('lodestar.project_workspaces._run',side_effect=transport),patch('lodestar.project_workspaces.credential',return_value=None):
            key=git.clone(self.root,self.snapshot(self.base))
        value=git.status(self.root,key)
        self.assertEqual(value['commit_sha'],self.base)
        self.assertEqual(value['branch'],'master')
        self.assertFalse(value['dirty'])
        self.assertTrue((Path(value['path'])/'.git').is_dir())


if __name__=='__main__': unittest.main()
