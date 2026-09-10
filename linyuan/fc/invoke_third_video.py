from invoke_reviewed_updates import main

if __name__ == '__main__':
    main(trigger='apply-reviewed-third-video-0910', state_key='reviewed_third_video_0910',
         bvids=('BV1hmYt6SEJd',), version_file='linyuan/fc/reviewed_0910/third-video-manifest.json',
         prefix='third-video-0910', report='third-video-verification.json')
