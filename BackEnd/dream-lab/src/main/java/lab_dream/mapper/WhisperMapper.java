package lab_dream.mapper;

import lab_dream.entity.Whisper;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;

/** 悄悄话的仓库。二周目「星野凛 认出你」靠的就是它：上一次留下的痕迹还在库里。 */
public interface WhisperMapper extends JpaRepository<Whisper, Long> {

    /** 最近一条悄悄话 */
    Optional<Whisper> findTopByOrderByCreatedAtDesc();
}
