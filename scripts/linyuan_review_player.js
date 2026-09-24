// Load only the selected movie. Hundreds of retained decoders exhaust browsers.
document.querySelectorAll('.review-player').forEach(box => {
  const video = box.querySelector('video');
  const button = box.querySelector('button');
  const status = box.querySelector('[role="status"]');
  let resumeAt = 0;
  video.releaseReviewMedia = () => {
    if (!video.hasAttribute('src')) return;
    resumeAt = video.currentTime || 0;
    video.pause();
    video.removeAttribute('src');
    video.load();
    button.hidden = false;
    status.textContent = resumeAt > 0 ? '已暂停，点击继续播放' : '';
  };
  async function activate() {
    document.querySelectorAll('.review-player video').forEach(other => {
      if (other !== video && other.releaseReviewMedia) other.releaseReviewMedia();
    });
    status.textContent = '正在载入视频…';
    if (!video.hasAttribute('src')) {
      video.src = video.dataset.src;
      video.load();
      if (resumeAt > 0) video.addEventListener('loadedmetadata', () => {
        video.currentTime = Math.min(resumeAt, Math.max(0, video.duration - 0.1));
      }, {once: true});
    }
    try { await video.play(); }
    catch (error) {
      if (error.name !== 'AbortError') {
        status.textContent = '网页内播放受限或文件加载失败，请用下方“单独打开视频”或本机浏览器地址。';
        button.hidden = false;
      }
    }
  }
  button.addEventListener('click', activate);
  video.addEventListener('click', () => { if (!video.hasAttribute('src')) activate(); });
  video.addEventListener('playing', () => { status.textContent = ''; button.hidden = true; });
  video.addEventListener('waiting', () => { status.textContent = '正在缓冲…'; });
  video.addEventListener('error', () => {
    const explanations = {1:'加载被中断',2:'视频文件未能读取',3:'视频解码失败',4:'浏览器不支持该视频，或视频路径不可访问'};
    status.textContent = (explanations[video.error?.code] || '视频无法加载') + '。请单独打开视频；只复制 HTML 文件不会携带视频。';
    button.hidden = false;
  });
});
if (location.protocol === 'file:') {
  document.querySelector('#playback-help')?.removeAttribute('hidden');
}
