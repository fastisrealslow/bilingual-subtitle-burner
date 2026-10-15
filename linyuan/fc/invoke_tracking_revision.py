from invoke_reviewed_updates import main

if __name__=='__main__':
    main(trigger='repair-reported-tracking-20261015',state_key='tracking_revision_20261015',
         bvids=('BV1UEpC6bEQB',),version_file='linyuan/fc/tracking_revision.py',
         prefix='tracking-20261015',report='tracking-revision-verification.json')
